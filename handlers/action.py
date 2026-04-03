"""
handlers/action.py — Task 선택, 검색, 인수인계 처리
"""

import json
import time

from services.notion import (
    get_all_tasks,
    search_tasks,
    get_handover_data,
    notion,
    _parse_task,
)
from services.slack import (
    build_log_step_modal,
    build_task_select_modal,
    build_handover_message,
    build_error_message,
)


def register_actions(app):

    @app.action("open_ilji_modal")
    def handle_open_ilji_modal(ack, body, client, logger):
        """일지 작성 버튼 클릭 → Task 선택 모달 오픈."""
        ack()

        try:
            # 로딩 모달을 먼저 열어 trigger_id 만료 방지
            loading_view = {
                "type": "modal",
                "title": {"type": "plain_text", "text": "📝 업무일지 작성"},
                "close": {"type": "plain_text", "text": "취소"},
                "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "⏳ Task 목록을 불러오는 중..."}}],
            }
            resp = client.views_open(
                trigger_id=body["trigger_id"],
                view=loading_view,
            )
            view_id = resp["view"]["id"]

            # ── 사용자 실명 기반 내 업무 우선 조회 ────────────────
            user_id = body.get("user", {}).get("id")
            try:
                user_info = client.users_info(user=user_id)
                real_name = user_info["user"]["profile"].get("real_name", "")
            except Exception:
                real_name = ""

            from services.notion import get_all_tasks, get_my_tasks
            
            if real_name:
                tasks = get_my_tasks(real_name)
                # 내 업무가 적으면 전체 인부 중 일부 충원 (최대 9개)
                if len(tasks) < 5:
                    all_tasks = get_all_tasks()
                    existing_ids = {t["id"] for t in tasks}
                    for t in all_tasks:
                        if t["id"] not in existing_ids:
                            tasks.append(t)
                            if len(tasks) >= 9:
                                break
            else:
                tasks = get_all_tasks()

            logger.info(f"일지 버튼 클릭 — Task {len(tasks)}개 구성 (사용자: {real_name})")

            modal = build_task_select_modal(tasks)
            client.views_update(view_id=view_id, view=modal)
        except Exception as e:
            logger.error(f"일지 모달 오픈 오류: {e}")

    @app.action("search_keyword")
    def handle_search_keyword(ack, body, client, logger):
        """검색어 입력 후 Enter → 노션 DB 검색 → 모달 갱신."""
        ack()

        try:
            view = body["view"]
            view_id = view["id"]
            values = view["state"]["values"]

            keyword = (values.get("block_search", {})
                       .get("search_keyword", {})
                       .get("value", "") or "").strip()

            if not keyword:
                return

            logger.info(f"Task 검색: '{keyword}'")
            tasks = search_tasks(keyword)
            logger.info(f"검색 결과: {len(tasks)}개")

            modal = build_task_select_modal(tasks, search_keyword=keyword)
            client.views_update(view_id=view_id, view=modal)

        except Exception as e:
            logger.error(f"Task 검색 처리 오류: {e}")

    @app.action("task_checkboxes")
    def handle_task_checkboxes_action(ack, body, logger):
        """checkboxes 상호작용 ack (dispatch_action 이벤트)."""
        ack()

    @app.view("modal_task_select")
    def handle_task_select(ack, body, client, logger):
        try:
            values = body["view"]["state"]["values"]
            selected_options = (values["block_task_select"]
                                ["task_checkboxes"]
                                ["selected_options"])

            if not selected_options:
                ack(response_action="errors", errors={
                    "block_task_select": "Task를 하나 이상 선택해 주세요."
                })
                return

            # 선택된 Task 목록 구성
            tasks = []
            for opt in selected_options:
                tasks.append({
                    "id": opt["value"],
                    "name": opt["text"]["text"],
                })

            logger.info(f"Task {len(tasks)}개 선택: "
                        f"{[t['name'] for t in tasks]}")

            metadata = {
                "tasks": tasks,
                "current": 0,
                "done": [],
            }
            metadata_json = json.dumps(metadata, ensure_ascii=False)

            first = tasks[0]
            is_new = (first["id"] == "NEW_TASK")
            total = len(tasks)

            modal = build_log_step_modal(
                metadata_json=metadata_json,
                task_name=first["name"],
                step=1,
                total=total,
                is_new=is_new,
            )
            ack(response_action="push", view=modal)

        except KeyError as e:
            logger.error(f"Task 선택 처리 오류: {e} / "
                         f"values={body.get('view', {}).get('state', {}).get('values', {})}")
            ack(response_action="errors", errors={
                "block_task_select": "Task를 선택해 주세요."
            })
        except Exception as e:
            logger.error(f"Task 선택 처리 오류: {e}")
            ack(response_action="errors", errors={
                "block_task_select": "오류가 발생했습니다. 다시 시도해 주세요."
            })

    # ════════════════════════════════════════════════════════════
    # 인수인계 모달 제출 처리
    # ════════════════════════════════════════════════════════════

    @app.view("modal_handover_select")
    def handle_handover_select(ack, body, client, logger):
        """인수인계 Task 선택 → 일지에서 이슈/리스크 추출 → DM 전송."""
        ack()

        user_id = body.get("user", {}).get("id")
        values = body["view"]["state"]["values"]

        selected = (values["block_handover_task"]
                    ["handover_task_select"]
                    ["selected_option"])

        if not selected:
            if user_id:
                client.chat_postMessage(
                    channel=user_id,
                    blocks=build_error_message("Task를 선택해 주세요.")
                )
            return

        task_id = selected["value"]
        task_label = selected["text"]["text"]
        logger.info(f"인수인계 요청: {task_label} ({task_id})")

        try:
            # Task 상세 정보 조회
            page = notion.pages.retrieve(page_id=task_id)
            task = _parse_task(page)

            # 일지에서 이슈/리스크 추출
            time.sleep(0.35)  # API 속도 제한 방지
            logs = get_handover_data(task_id)

            blocks = build_handover_message(task, logs)
            client.chat_postMessage(
                channel=user_id,
                text=f"📋 인수인계 초안 — {task['name']}",
                blocks=blocks,
            )
            logger.info(f"인수인계 초안 전송 완료: {task['name']} (이슈/리스크 {len(logs)}건)")

        except Exception as e:
            logger.error(f"인수인계 처리 오류: {e}")
            if user_id:
                client.chat_postMessage(
                    channel=user_id,
                    blocks=build_error_message(str(e))
                )
