"""
handlers/message.py — 일지 채널 메시지 자동 처리
=================================================
전용 채널에 텍스트 메시지를 작성하면 자동으로 노션에 기록.

메시지 형식:
  첫 줄  = Task명 (노션에서 검색하여 매칭)
  나머지 = 일지 내용 (노션 "완료" 항목에 기록)
"""

import logging

from config import SLACK_LOG_CHANNEL_ID
from services.notion import (
    search_tasks,
    create_task,
    append_daily_log,
    get_notion_user_id,
)

logger = logging.getLogger(__name__)


def register_messages(app):
    """app.py에서 호출해 메시지 이벤트 핸들러를 등록합니다."""

    @app.event("message")
    def handle_message(event, client, logger):
        # ── 필터링 ─────────────────────────────────────────
        # 일지 전용 채널만 처리
        if event.get("channel") != SLACK_LOG_CHANNEL_ID:
            return

        # 봇 메시지·서브타입(편집/삭제 등) 무시
        if event.get("subtype") or event.get("bot_id"):
            return

        text = (event.get("text") or "").strip()
        if not text:
            return

        user_id = event.get("user")
        channel = event.get("channel")
        ts = event.get("ts")  # 스레드 응답·이모지에 필요

        # ── 메시지 파싱 ────────────────────────────────────
        lines = text.split("\n")
        task_name = lines[0].strip()
        content = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

        if not task_name:
            return

        # ── 슬랙 사용자 실명 조회 ──────────────────────────
        try:
            user_info = client.users_info(user=user_id)
            user_real_name = user_info["user"]["profile"].get("real_name", "")
        except Exception:
            user_real_name = ""

        logger.info(f"일지 채널 메시지: user={user_real_name or user_id}, task='{task_name}'")

        try:
            # ── 노션 Task 검색 ─────────────────────────────
            tasks = search_tasks(task_name)

            if tasks:
                # 첫 번째 매칭 Task 사용
                matched = tasks[0]
                page_id = matched["id"]
                matched_name = matched["name"]
                task_url = matched["url"]
                is_new = False
                logger.info(f"Task 매칭: '{matched_name}' (id={page_id})")
            else:
                # 매칭 없음 → 새 Task 생성
                notion_user_id = get_notion_user_id(user_real_name)
                task = create_task(
                    task_name=task_name,
                    assignee_notion_id=notion_user_id,
                )
                if not task:
                    client.chat_postMessage(
                        channel=channel,
                        thread_ts=ts,
                        text=f"❌ Task 생성에 실패했습니다. 다시 시도해 주세요.",
                    )
                    return

                page_id = task["id"]
                matched_name = task["name"]
                task_url = task["url"]
                is_new = True
                logger.info(f"새 Task 생성: '{matched_name}'")

            # ── 일지 추가 ─────────────────────────────────
            import datetime as _dt
            log = {
                "author": user_real_name or user_id,
                "log_date": _dt.date.today().isoformat(),
                "completed": content or "-",
                "tomorrow": "-",
                "issues": "-",
                "risk": "-",
            }
            ok = append_daily_log(
                task_id=page_id,
                task_name=matched_name,
                log_date=log["log_date"],
                completed=log["completed"],
                tomorrow=log["tomorrow"],
                issues=log["issues"],
                risk=log["risk"],
                author_slack=log["author"],
                is_new_task=is_new,
                manual_completed=log["completed"]
            )

            if ok:
                # 성공: ✅ 이모지 (권한 없으면 무시) + 스레드 안내
                try:
                    client.reactions_add(
                        channel=channel,
                        timestamp=ts,
                        name="white_check_mark",
                    )
                except Exception:
                    pass
                action_text = "새 Task를 생성하고 일지를 기록" if is_new else "일지를 기록"
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=ts,
                    text=f"✅ {action_text}했습니다.\n*{matched_name}*\n<{task_url}|📎 노션에서 확인하기>",
                )
            else:
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=ts,
                    text="❌ 노션 일지 기록에 실패했습니다. 잠시 후 다시 시도해 주세요.",
                )

        except Exception as e:
            logger.error(f"일지 채널 메시지 처리 오류: {e}")
            try:
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=ts,
                    text=f"❌ 오류가 발생했습니다.\n{e}",
                )
            except Exception:
                pass
