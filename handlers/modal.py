"""
handlers/modal.py — 일지 입력 모달 제출 처리 (멀티 Task 지원)
==============================================================
핵심 흐름:
  1. private_metadata JSON 파싱 + 입력값 파싱 (빠름)
  2. ack() 즉시 호출 (3초 제한 준수)
  3. 느린 작업: 슬랙 실명 조회 + 노션 저장 + DM 발송
"""

import datetime
import json

from services.notion import (
    append_daily_log,
    create_task,
    get_notion_user_id,
)
from services.slack import (
    build_log_step_modal,
    build_multi_success_message,
    build_success_message,
    build_error_message,
)


def register_modals(app):
    """app.py에서 호출해 모달 핸들러를 등록합니다."""

    @app.view("modal_log_submit")
    def handle_log_submit(ack, body, client, logger):
        """
        단계별 일지 제출 처리.

        private_metadata JSON 구조:
        {
          "tasks": [{"id": "...", "name": "..."}, ...],
          "current": 0,
          "done": [{"name": "...", "url": "...", "is_new": false}, ...]
        }
        """
        user_id = body.get("user", {}).get("id")
        values = body.get("view", {}).get("state", {}).get("values", {})
        raw_metadata = body.get("view", {}).get("private_metadata", "")

        # ── metadata 파싱 (빠름) ──────────────────────────────
        try:
            meta = json.loads(raw_metadata)
        except (json.JSONDecodeError, TypeError):
            logger.error(f"metadata 파싱 실패: {raw_metadata}")
            ack(response_action="clear")
            if user_id:
                client.chat_postMessage(
                    channel=user_id,
                    blocks=build_error_message("일지 처리 중 오류가 발생했습니다.")
                )
            return

        tasks = meta["tasks"]
        current = meta["current"]
        done = meta["done"]
        total = len(tasks)
        current_task = tasks[current]
        task_id = current_task["id"]
        is_new = (task_id == "NEW_TASK")

        # ── 입력값 파싱 (빠름) ────────────────────────────────
        def get_val(block: str, action: str) -> str:
            return (values.get(block, {})
                    .get(action, {})
                    .get("value") or "")

        def get_date(block: str, action: str) -> str | None:
            return (values.get(block, {})
                    .get(action, {})
                    .get("selected_date"))

        def get_select(block: str, action: str) -> str | None:
            opt = (values.get(block, {})
                   .get(action, {})
                   .get("selected_option"))
            return opt["value"] if opt else None

        def get_user_select(block: str, action: str) -> str | None:
            return (values.get(block, {})
                    .get(action, {})
                    .get("selected_user"))

        # 새 Task 이름 검증 (ack 전에 처리해야 errors 응답 가능)
        new_name = None
        if is_new:
            new_name = get_val("block_new_task_name", "new_task_name")
            if not new_name:
                ack(response_action="errors", errors={
                    "block_new_task_name": "업무명을 입력해 주세요."
                })
                return

        # ── done에 현재 Task 추가 (기존 Task는 URL 계산 가능) ─
        next_idx = current + 1

        if is_new:
            done.append({"name": new_name, "url": "", "is_new": True})
        else:
            done.append({
                "name": current_task["name"],
                "url": f"https://notion.so/{task_id.replace('-', '')}",
                "is_new": False,
            })

        # ══════════════════════════════════════════════════════
        # ACK 먼저 호출 — Slack 3초 제한 준수
        # ══════════════════════════════════════════════════════
        if next_idx < total:
            meta["current"] = next_idx
            meta["done"] = done
            metadata_json = json.dumps(meta, ensure_ascii=False)

            next_task = tasks[next_idx]
            modal = build_log_step_modal(
                metadata_json=metadata_json,
                task_name=next_task["name"],
                step=next_idx + 1,
                total=total,
                is_new=(next_task["id"] == "NEW_TASK"),
            )
            ack(response_action="update", view=modal)
        else:
            ack(response_action="clear")

        # ══════════════════════════════════════════════════════
        # 느린 작업: 슬랙 실명 조회 + 노션 저장 + DM
        # ══════════════════════════════════════════════════════
        user_id = body.get("user", {}).get("id")
        
        # 선택된 담당자 정보 (사용자 지정 배정)
        selected_assignee_id = get_user_select("block_assignee", "assignee_select")
        target_user_id = selected_assignee_id or user_id
        
        try:
            target_info = client.users_info(user=target_user_id)
            target_name = target_info["user"]["profile"].get("real_name", "") or target_info["user"].get("name", "")
        except Exception:
            target_name = ""

        # 일지 작성자 정보 (기존 로직 유지)
        try:
            author_info = client.users_info(user=user_id)
            author_name = author_info["user"]["profile"].get("real_name", "") or author_info["user"].get("name", "")
        except Exception:
            author_name = ""

        log_date = get_date("block_log_date", "log_date")
        log = {
            "author":       author_name,
            "log_date":     log_date or datetime.date.today().isoformat(),
            "completed":    get_val("block_completed",    "completed"),
            "tomorrow":     get_val("block_tomorrow",     "tomorrow"),
            "consultation": get_val("block_consultation", "consultation"),
            "issues":       get_val("block_issues",       "issues"),
            "risk":         get_val("block_risk",         "risk"),
        }

        logger.info(f"일지 제출 ({current+1}/{total}): "
                    f"{user_name}, task={current_task['name']}")

        try:
            # 상태 변경 처리 (선택된 경우)
            new_status = get_select("block_status", "status_select")
            if not is_new:
                from services.notion import update_task_status, update_task_assignee
                if new_status:
                    update_task_status(task_id, new_status)
                # 명시적 담당자 배정 (선택된 담당자로 업데이트)
                if target_name:
                    update_task_assignee(task_id, target_name)

            if is_new:
                new_deadline = get_date("block_new_task_deadline",
                                        "new_task_deadline")
                new_client = get_select("block_new_task_client",
                                        "new_task_client")
                new_phase = get_select("block_new_task_phase",
                                       "new_task_phase")

                notion_user_id = get_notion_user_id(target_name or author_name)

                task = create_task(
                    task_name=new_name,
                    assignee_notion_id=notion_user_id,
                    deadline=new_deadline,
                    client_name=new_client,
                    phase=new_phase,
                )

                if not task:
                    client.chat_postMessage(
                        channel=user_id,
                        blocks=build_error_message("Task 생성에 실패했습니다.")
                    )
                    return

                append_daily_log(
                    task_id=task["id"],
                    task_name=task["name"],
                    log_date=log["log_date"],
                    completed=log["completed"],
                    tomorrow=log["tomorrow"],
                    consultation=log["consultation"],
                    issues=log["issues"],
                    risk=log["risk"],
                    author_slack=author_name,
                )

                # 마지막 단계면 done 갱신 (DM에서 실제 URL 표시)
                if next_idx >= total:
                    done[-1]["name"] = task["name"]
                    done[-1]["url"] = task["url"]
            else:
                append_daily_log(
                    task_id=task_id,
                    task_name=current_task["name"],
                    log_date=log["log_date"],
                    completed=log["completed"],
                    tomorrow=log["tomorrow"],
                    consultation=log["consultation"],
                    issues=log["issues"],
                    risk=log["risk"],
                    status_update=new_status or "",
                    author_slack=author_name,
                )

            logger.info(f"일지 기록 완료 ({current+1}/{total}): "
                        f"{current_task['name']}")

            # 마지막 단계: DM 발송
            if next_idx >= total:
                if len(done) == 1:
                    blocks = build_success_message(
                        done[0]["name"], done[0]["url"], done[0]["is_new"]
                    )
                else:
                    blocks = build_multi_success_message(done)

                logger.info(f"일지 기록 완료: {len(done)}건")
                client.chat_postMessage(channel=user_id, blocks=blocks)

        except Exception as e:
            logger.error(f"일지 제출 처리 오류: {e}")
            if user_id:
                client.chat_postMessage(
                    channel=user_id,
                    blocks=build_error_message(str(e))
                )
