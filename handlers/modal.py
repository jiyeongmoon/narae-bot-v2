"""
handlers/modal.py — 일지 입력 모달 제출 처리 (멀티 Task + To-do 연동)
==============================================================
핵심 흐름:
  1. private_metadata JSON 파싱 + 입력값 파싱 (빠름)
  2. ack() 즉시 호출 (3초 제한 준수)
  3. 느린 작업: 슬랙 실명 조회 + 노션 저장 + To-do 업데이트 + DM 발송
"""

import datetime
import json

from services.notion import (
    save_log as append_daily_log,
    create_task,
    get_notion_user_id,
    get_task_todos,
    update_todo_checked,
    replace_text_pattern_todos,
    update_task_status,
    update_task_assignee,
    update_task_assignee_by_notion_id,
    CLIENT_TO_PREFIX,
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
          "tasks": [{"id": "...", "name": "...", "status": "..."}, ...],
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
        is_new = task_id.startswith("NEW_TASK")

        # ── 입력값 파싱 헬퍼 ──────────────────────────────────
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

        # ── To-do 체크 항목 파싱 ──────────────────────────────
        checked_todo_ids = set()
        todo_opts = (values.get("block_todo_check", {})
                     .get("todo_checkboxes", {})
                     .get("selected_options") or [])
        for opt in todo_opts:
            checked_todo_ids.add(opt["value"])

        # 새 Task 이름 검증
        new_name = None
        new_notion_assignee_id = None
        if is_new:
            # 발주처: 직접 입력이 있으면 우선, 없으면 목록 선택값
            client_text   = get_val("block_new_task_client_text", "new_task_client_text") or ""
            client_select = get_select("block_new_task_client",   "new_task_client") or ""
            task_client   = (client_text or client_select).strip()

            sub         = get_val("block_new_task_sub",    "new_task_sub")    or ""
            outcome     = get_val("block_new_task_name",   "new_task_name")   or ""
            
            # 발주처에서 대분류(prefix) 자동 파생
            prefix = CLIENT_TO_PREFIX.get(task_client, task_client) if task_client else ""
            if not task_client or not sub or not outcome:
                errors = {}
                if not task_client: errors["block_new_task_client"] = "발주처를 선택해 주세요."
                if not sub:         errors["block_new_task_sub"]    = "소분류를 입력해 주세요."
                if not outcome:     errors["block_new_task_name"]   = "결과물명을 입력해 주세요."
                ack(response_action="errors", errors=errors)
                return
            new_name = f"[{prefix}_{sub}] {outcome}"

        # ── done에 현재 Task 추가 ─────────────────────────────
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
            next_is_new = next_task["id"].startswith("NEW_TASK")

            # 다음 Task의 To-do 미리 조회 (빠른 ACK 위해 별도 처리)
            next_todos = []
            if not next_is_new:
                try:
                    next_todos = get_task_todos(next_task["id"])
                except Exception:
                    next_todos = []

            try:
                modal = build_log_step_modal(
                    metadata_json=metadata_json,
                    task_name=next_task["name"],
                    step=next_idx + 1,
                    total=total,
                    user_id=user_id,        # ← 필수: 담당자 블록 구성에 필요
                    is_new=next_is_new,
                    current_status=next_task.get("status"),
                    todos=next_todos,
                )
                ack(response_action="update", view=modal)
            except Exception as modal_err:
                logger.error(f"다음 스텝 모달 빌드 실패: {modal_err}", exc_info=True)
                ack()   # 모달 닫기로 fallback
        else:
            ack(response_action="clear")

        # ══════════════════════════════════════════════════════
        # 느린 작업: 슬랙 실명 조회 + 노션 저장 + To-do 업데이트 + DM
        # ══════════════════════════════════════════════════════
        
        # 1. 진행 상태 알림 즉시 발송
        progress_msg = None
        try:
            progress_msg = client.chat_postMessage(
                channel=user_id,
                text=f"⏳ *{current_task['name']}* 일지를 기록하고 있습니다... 잠시만 기다려 주세요."
            )
        except Exception as pe:
            logger.error(f"진행 알림 발송 실패: {pe}")

        try:
            # 선택된 담당자 정보
            selected_assignee_id = get_user_select("block_assignee", "assignee_select")
            target_user_id = selected_assignee_id or user_id

            try:
                target_info = client.users_info(user=target_user_id)
                target_name = target_info["user"]["profile"].get("real_name", "") or target_info["user"].get("name", "")
            except Exception:
                target_name = ""

            # 일지 작성자 정보
            try:
                author_info = client.users_info(user=user_id)
                author_name = author_info["user"]["profile"].get("real_name", "") or author_info["user"].get("name", "")
            except Exception:
                author_name = ""

            # ── To-do 기반 완료/예정 자동 조합 ───────────────────
            s = current + 1   # 현재 스텝 번호 (block_id 접미사와 동일)
            manual_completed = get_val(f"block_completed_{s}", f"completed_{s}")
            manual_tomorrow  = get_val(f"block_tomorrow_{s}",  f"tomorrow_{s}")

            auto_completed_lines = []

            if not is_new and checked_todo_ids is not None:
                try:
                    all_todos = get_task_todos(task_id)
                    for todo in all_todos:
                        # 이번에 '새롭게' 체크한 항목만 "오늘 완료"에 기록 (기존 체크 항목 제외)
                        if todo["id"] in checked_todo_ids and not todo.get("checked"):
                            auto_completed_lines.append(f"• {todo['text']}")
                        #   → replace_text_pattern_todos()가 To-do 섹션에 실제 to_do 블록으로 삽입
                except Exception as e:
                    logger.warning(f"To-do 재조회 실패: {e}")

            # 오늘 완료: 체크된 항목(auto) + 수동 입력(manual) 병합
            combined_completed = "\n".join(filter(None, [
                ("\n".join(auto_completed_lines)) if auto_completed_lines else "",
                manual_completed
            ]))
            # 내일 예정: 수동 입력만 (미체크 항목은 to_do 블록으로 직접 삽입)
            combined_tomorrow = manual_tomorrow

            log_date = get_date(f"block_log_date_{s}", f"log_date_{s}")
            log = {
                "author":       author_name,
                "log_date":     log_date or datetime.date.today().isoformat(),
                "completed":    combined_completed,
                "tomorrow":     combined_tomorrow,
                "consultation": get_val(f"block_consultation_{s}", f"consultation_{s}"),
                "issues":       get_val(f"block_issues_{s}",       f"issues_{s}"),
                "risk":         get_val(f"block_risk_{s}",         f"risk_{s}"),
            }

            new_status = get_select("block_status", "status_select")

            if is_new:
                new_deadline = get_date("block_new_task_deadline", "new_task_deadline")
                # 발주처: 직접입력이 있으면 우선, 없으면 목록 선택값
                new_client_text   = get_val("block_new_task_client_text", "new_task_client_text")
                new_client_select = get_select("block_new_task_client",   "new_task_client")
                new_client        = (new_client_text or new_client_select or "").strip() or None
                new_phase    = get_select("block_new_task_phase",  "new_task_phase")
                new_initial_status = get_select("block_new_task_status", "new_task_status") or "🙏 진행 예정"

                notion_user_id = get_notion_user_id(target_name or author_name)
                task = create_task(
                    task_name=new_name,
                    assignee_notion_id=notion_user_id,
                    assignee_name=target_name or author_name,   # ← 본문 담당자 표시용
                    deadline=new_deadline,
                    client_name=new_client,
                    phase=new_phase,
                    initial_status=new_initial_status,
                )

                if task:
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
                        is_new_task=True,   # 새 Task: 완료+내일예정 모두 To-do에 추가
                    )
                    done[-1]["name"] = task["name"]
                    done[-1]["url"]  = task["url"]
            else:
                # 1. 상태/담당자 업데이트 (Notion User ID 직접 사용)
                if new_status: update_task_status(task_id, new_status)
                if selected_assignee_id:
                    notion_uid = get_notion_user_id(target_name)
                    if notion_uid:
                        update_task_assignee_by_notion_id(task_id, notion_uid)
                    else:
                        # fallback: 이름 기반 매칭 시도
                        update_task_assignee(task_id, target_name)
                
                # 2. To-do 처리 (기존 Task만, 새 Task는 생략)
                if not is_new:
                    try:
                        all_todos_now = get_task_todos(task_id)
                        c_ids = checked_todo_ids or set()

                        # 2-a. 진짜 Notion to_do 블록: 체크된 것만 checked=True 업데이트
                        for todo in all_todos_now:
                            if todo["block_type"] == "to_do" and todo["id"] in c_ids and not todo.get("checked"):
                                update_todo_checked(todo["id"], True)

                        # 2-b. text_pattern(- [ ] 텍스트) 블록 전체를 실제 to_do 블록으로 교체
                        #      - 체크된 항목: 텍스트 블록 삭제 (이미 '오늘 완료' 로그에 반영)
                        #      - 미체크 항목: 텍스트 블록 삭제 → 새 to_do 블록 삽입
                        replace_text_pattern_todos(task_id, all_todos_now, c_ids)
                    except Exception as te:
                        logger.warning(f"To-do 업데이트 무시됨: {te}")

                # 3. 일지 기록
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
                    manual_completed=manual_completed,
                )

            # 마지막 단계면 완료 메시지 발송
            if next_idx >= total:
                if len(done) == 1:
                    msg_name = done[0]["name"]
                    blks = build_success_message(msg_name, done[0]["url"], done[0]["is_new"])
                    fallback = f"✅ 일지가 기록됐습니다! {msg_name}"
                    client.chat_postMessage(channel=user_id, text=fallback, blocks=blks)
                else:
                    blks, fallback = build_multi_success_message(done)
                    client.chat_postMessage(channel=user_id, text=fallback, blocks=blks)
                
                # 진행 알림 삭제
                if progress_msg:
                    try: client.chat_delete(channel=user_id, ts=progress_msg["ts"])
                    except: pass

        except Exception as e:
            logger.error(f"일지 제출 처리 오류: {e}")
            # 진행 알림 삭제 시도
            if progress_msg:
                try: client.chat_delete(channel=user_id, ts=progress_msg["ts"])
                except: pass
            
            # 구체적인 에러를 슬랙으로 알림
            client.chat_postMessage(
                channel=user_id,
                text=f"❌ *일지 기록 실패*\n오류 내용: `{str(e)}`"
            )
