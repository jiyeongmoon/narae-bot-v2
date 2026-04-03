"""
handlers/action.py ??Task ? íƒ, ê²€?? ?¸ìˆ˜?¸ê³„ ì²˜ë¦¬
"""

import json
import time

from services.notion import (
    get_all_tasks,
    search_tasks,
    get_handover_data,
    notion_client,
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
        """?¼ì? ?‘ì„± ë²„íŠ¼ ?´ë¦­ ??Task ? íƒ ëª¨ë‹¬ ?¤í”ˆ."""
        ack()

        try:
            # ë¡œë”© ëª¨ë‹¬??ë¨¼ì? ?´ì–´ trigger_id ë§Œë£Œ ë°©ì?
            loading_view = {
                "type": "modal",
                "title": {"type": "plain_text", "text": "?“ ?…ë¬´?¼ì? ?‘ì„±"},
                "close": {"type": "plain_text", "text": "ì·¨ì†Œ"},
                "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "??Task ëª©ë¡??ë¶ˆëŸ¬?¤ëŠ” ì¤?.."}}],
            }
            resp = client.views_open(
                trigger_id=body["trigger_id"],
                view=loading_view,
            )
            view_id = resp["view"]["id"]

            # ?€?€ ?¬ìš©???¤ëª… ê¸°ë°˜ ???…ë¬´ ?°ì„  ì¡°íšŒ ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
            user_id = body.get("user", {}).get("id")
            try:
                user_info = client.users_info(user=user_id)
                real_name = user_info["user"]["profile"].get("real_name", "")
            except Exception:
                real_name = ""

            from services.notion import get_all_tasks, get_my_tasks
            
            if real_name:
                tasks = get_my_tasks(real_name)
                # ???…ë¬´ê°€ ?ìœ¼ë©??„ì²´ ?¸ë? ì¤??¼ë? ì¶©ì› (ìµœë? 9ê°?
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

            logger.info(f"?¼ì? ë²„íŠ¼ ?´ë¦­ ??Task {len(tasks)}ê°?êµ¬ì„± (?¬ìš©?? {real_name})")

            modal = build_task_select_modal(tasks)
            client.views_update(view_id=view_id, view=modal)
        except Exception as e:
            logger.error(f"?¼ì? ëª¨ë‹¬ ?¤í”ˆ ?¤ë¥˜: {e}")

    @app.action("search_keyword")
    def handle_search_keyword(ack, body, client, logger):
        """ê²€?‰ì–´ ?…ë ¥ ??Enter ???¸ì…˜ DB ê²€????ëª¨ë‹¬ ê°±ì‹ ."""
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

            logger.info(f"Task ê²€?? '{keyword}'")
            tasks = search_tasks(keyword)
            logger.info(f"ê²€??ê²°ê³¼: {len(tasks)}ê°?)

            modal = build_task_select_modal(tasks, search_keyword=keyword)
            client.views_update(view_id=view_id, view=modal)

        except Exception as e:
            logger.error(f"Task ê²€??ì²˜ë¦¬ ?¤ë¥˜: {e}")

    @app.action("task_checkboxes")
    def handle_task_checkboxes_action(ack, body, logger):
        """checkboxes ?í˜¸?‘ìš© ack (dispatch_action ?´ë²¤??."""
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
                    "block_task_select": "Taskë¥??˜ë‚˜ ?´ìƒ ? íƒ??ì£¼ì„¸??"
                })
                return

            # ? íƒ??Task ëª©ë¡ êµ¬ì„±
            tasks = []
            for opt in selected_options:
                tasks.append({
                    "id": opt["value"],
                    "name": opt["text"]["text"],
                })

            logger.info(f"Task {len(tasks)}ê°?? íƒ: "
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
            logger.error(f"Task ? íƒ ì²˜ë¦¬ ?¤ë¥˜: {e} / "
                         f"values={body.get('view', {}).get('state', {}).get('values', {})}")
            ack(response_action="errors", errors={
                "block_task_select": "Taskë¥?? íƒ??ì£¼ì„¸??"
            })
        except Exception as e:
            logger.error(f"Task ? íƒ ì²˜ë¦¬ ?¤ë¥˜: {e}")
            ack(response_action="errors", errors={
                "block_task_select": "?¤ë¥˜ê°€ ë°œìƒ?ˆìŠµ?ˆë‹¤. ?¤ì‹œ ?œë„??ì£¼ì„¸??"
            })

    # ?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•
    # ?¸ìˆ˜?¸ê³„ ëª¨ë‹¬ ?œì¶œ ì²˜ë¦¬
    # ?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•?â•

    @app.view("modal_handover_select")
    def handle_handover_select(ack, body, client, logger):
        """?¸ìˆ˜?¸ê³„ Task ? íƒ ???¼ì??ì„œ ?´ìŠˆ/ë¦¬ìŠ¤??ì¶”ì¶œ ??DM ?„ì†¡."""
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
                    blocks=build_error_message("Taskë¥?? íƒ??ì£¼ì„¸??")
                )
            return

        task_id = selected["value"]
        task_label = selected["text"]["text"]
        logger.info(f"?¸ìˆ˜?¸ê³„ ?”ì²­: {task_label} ({task_id})")

        try:
            # Task ?ì„¸ ?•ë³´ ì¡°íšŒ
            page = notion_client.pages.retrieve(page_id=task_id)
            task = _parse_task(page)

            # ?¼ì??ì„œ ?´ìŠˆ/ë¦¬ìŠ¤??ì¶”ì¶œ
            time.sleep(0.35)  # API ?ë„ ?œí•œ ë°©ì?
            logs = get_handover_data(task_id)

            blocks = build_handover_message(task, logs)
            client.chat_postMessage(
                channel=user_id,
                text=f"?“‹ ?¸ìˆ˜?¸ê³„ ì´ˆì•ˆ ??{task['name']}",
                blocks=blocks,
            )
            logger.info(f"?¸ìˆ˜?¸ê³„ ì´ˆì•ˆ ?„ì†¡ ?„ë£Œ: {task['name']} (?´ìŠˆ/ë¦¬ìŠ¤??{len(logs)}ê±?")

        except Exception as e:
            logger.error(f"?¸ìˆ˜?¸ê³„ ì²˜ë¦¬ ?¤ë¥˜: {e}")
            if user_id:
                client.chat_postMessage(
                    channel=user_id,
                    blocks=build_error_message(str(e))
                )
