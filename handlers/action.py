# -*- coding: utf-8 -*-
import json, time
from services.notion import (
    get_all_tasks, search_tasks, get_handover_data, notion_client,
    _parse_task, get_my_tasks, get_task_todos, update_todo_checked
)
from services.slack import build_log_step_modal, build_task_select_modal, build_handover_message, build_error_message

def register_actions(app):

    @app.action("open_ilji_modal")
    def handle_open_ilji_modal(ack, body, client, logger):
        ack()
        try:
            lv = {"type":"modal","title":{"type":"plain_text","text":"업무일지 작성"},"close":{"type":"plain_text","text":"취소"},"blocks":[{"type":"section","text":{"type":"mrkdwn","text":"로딩중..."}}]}
            resp = client.views_open(trigger_id=body["trigger_id"],view=lv)
            vid = resp["view"]["id"]
            uid = body.get("user",{}).get("id")
            try:
                ui = client.users_info(user=uid)
                rn = ui["user"]["profile"].get("real_name","")
            except:
                rn = ""
            if rn:
                tasks = get_my_tasks(rn)
                if len(tasks) < 5:
                    at = get_all_tasks()
                    eids = {t["id"] for t in tasks}
                    for t in at:
                        if t["id"] not in eids:
                            tasks.append(t)
                            if len(tasks) >= 9: break
            else:
                tasks = get_all_tasks()
            logger.info(f"tasks={len(tasks)} user={rn}")
            client.views_update(view_id=vid, view=build_task_select_modal(tasks, user_real_name=rn))
        except Exception as e: logger.error(f"modal err: {e}")

    @app.action("search_keyword")
    def handle_search_keyword(ack, body, client, logger):
        ack()
        try:
            view=body["view"]; vid=view["id"]; vals=view["state"]["values"]
            kw=(vals.get("block_search",{}).get("search_keyword",{}).get("value","") or "").strip()

            uid = body.get("user", {}).get("id")
            try:
                ui = client.users_info(user=uid)
                rn = ui["user"]["profile"].get("real_name", "")
            except:
                rn = None

            # 검색어가 없으면 기본 내 업무 화면으로 복원
            if not kw:
                if rn:
                    tasks = get_my_tasks(rn)
                    if len(tasks) < 5:
                        at = get_all_tasks()
                        eids = {t["id"] for t in tasks}
                        for t in at:
                            if t["id"] not in eids:
                                tasks.append(t)
                                if len(tasks) >= 9: break
                else:
                    tasks = get_all_tasks()
                logger.info(f"search cleared, reset to {len(tasks)} tasks for {rn}")
                client.views_update(view_id=vid, view=build_task_select_modal(tasks, user_real_name=rn or ""))
                return

            tasks=search_tasks(kw, slack_display_name=rn)
            logger.info(f"search {kw} for {rn}: {len(tasks)}")
            client.views_update(view_id=vid, view=build_task_select_modal(tasks, user_real_name=rn or "", search_keyword=kw))
        except Exception as e: logger.error(f"search err: {e}")

    @app.action("filter_assignee")
    def handle_filter_assignee(ack, body, client, logger):
        """담당자 필터 변경 시 모달 갱신"""
        ack()
        try:
            view = body["view"]
            view_id = view["id"]
            selected_user_id = body["actions"][0]["selected_user"]
            
            try:
                ui = client.users_info(user=selected_user_id)
                filter_name = ui["user"]["profile"].get("real_name", "") or ui["user"].get("name", "")
            except Exception:
                filter_name = ""

            uid = body.get("user", {}).get("id")
            try:
                curr_ui = client.users_info(user=uid)
                curr_rn = curr_ui["user"]["profile"].get("real_name", "")
            except:
                curr_rn = ""

            tasks = get_my_tasks(filter_name)
            logger.info(f"filter assignee '{filter_name}': {len(tasks)} tasks")
            
            client.views_update(
                view_id=view_id, 
                view=build_task_select_modal(
                    tasks, 
                    user_real_name=curr_rn, 
                    filter_user_id=selected_user_id,
                    filter_user_name=filter_name
                )
            )
        except Exception as e:
            logger.error(f"filter err: {e}")

    @app.action("task_checkboxes")
    def handle_task_checkboxes_action(ack, body, logger): ack()

    @app.view("modal_task_select")
    def handle_task_select(ack, body, client, logger):
        try:
            vals = body["view"]["state"]["values"]

            # 모든 섹션의 체크박스 선택값을 합산
            selected = []
            for block_id, data in vals.items():
                if block_id.startswith("block_") and any(k in block_id for k in 
                    ("checkboxes","tasks","results","other_","new_task","my_tasks","unassigned")):
                    for action_id, action_data in data.items():
                        opts = action_data.get("selected_options")
                        if opts:
                            selected.extend(opts)

            if not selected:
                ack(response_action="errors", errors={
                    "block_search": "Task를 하나 이상 선택해 주세요."
                })
                return

            # JSON 형식 value 파싱 ({"id": ..., "status": ...} 또는 단순 문자열)
            def parse_option(opt):
                try:
                    data = json.loads(opt["value"])
                    return {
                        "id": data["id"],
                        "name": opt["text"]["text"],
                        "status": data.get("status", ""),
                    }
                except (json.JSONDecodeError, KeyError):
                    return {"id": opt["value"], "name": opt["text"]["text"], "status": ""}

            tasks = [parse_option(o) for o in selected]
            logger.info(f"Task {len(tasks)}개 선택: {[t['name'] for t in tasks]}")

            first = tasks[0]
            is_new = (first["id"] == "NEW_TASK")

            # 첫 번째 Task의 To-do 조회
            todos = []
            if not is_new and first["id"] != "NEW_TASK":
                try:
                    todos = get_task_todos(first["id"])
                    logger.info(f"To-do 조회: {len(todos)}개")
                except Exception as e:
                    logger.warning(f"To-do 조회 실패 (무시): {e}")

            meta = {"tasks": tasks, "current": 0, "done": []}
            meta_json = json.dumps(meta, ensure_ascii=False)

            modal = build_log_step_modal(
                metadata_json=meta_json,
                task_name=first["name"],
                step=1,
                total=len(tasks),
                user_id=body.get("user", {}).get("id"),
                is_new=is_new,
                current_status=first.get("status"),
                todos=todos,
            )
            ack(response_action="push", view=modal)

        except Exception as e:
            logger.error(f"task select err: {e}")
            ack(response_action="errors", errors={
                "block_search": "오류가 발생했습니다. 다시 시도해 주세요."
            })


    @app.view("modal_handover_select")
    def handle_handover_select(ack, body, client, logger):
        ack()
        uid=body.get("user",{}).get("id")
        vals=body["view"]["state"]["values"]
        sel=vals["block_handover_task"]["handover_task_select"]["selected_option"]
        if not sel:
            if uid: client.chat_postMessage(channel=uid,blocks=build_error_message("Task 선택 필요."))
            return
        tid=sel["value"]; tlabel=sel["text"]["text"]
        logger.info(f"handover: {tlabel}")
        try:
            page=notion_client.pages.retrieve(page_id=tid)
            task=_parse_task(page)
            time.sleep(0.35)
            logs=get_handover_data(tid)
            blocks=build_handover_message(task,logs)
            client.chat_postMessage(channel=uid,text="인수인계 초안",blocks=blocks)
        except Exception as e:
            logger.error(f"handover err: {e}")
            if uid: client.chat_postMessage(channel=uid,blocks=build_error_message(str(e)))

    @app.action("remind_at_5pm")
    def handle_remind_at_5pm(ack, body, client, logger):
        """17:00 리마인드 예약 처리"""
        ack()
        uid = body.get("user", {}).get("id")
        task_info = body.get("actions", [{}])[0].get("value", "업무")
        
        import datetime
        now = datetime.datetime.now()
        target = now.replace(hour=17, minute=0, second=0, microsecond=0)
        if now >= target:
            target += datetime.timedelta(days=1)
        
        try:
            client.chat_scheduleMessage(
                channel=uid,
                post_at=int(target.timestamp()),
                text=f"🕐 *5시 리마인드*: '{task_info}' 관련하여 추가로 기록할 내용이 있는지 확인해 보세요!"
            )
            channel_id = body.get("channel", {}).get("id")
            if channel_id:
                client.chat_postEphemeral(channel=channel_id, user=uid, text=f"✅ {target.strftime('%H:%M')}에 리마인드가 예약되었습니다.")
            else:
                client.chat_postMessage(channel=uid, text=f"✅ {target.strftime('%H:%M')}에 리마인드가 예약되었습니다.")
        except Exception as e:
            logger.error(f"remind schedule err: {e}")
            if uid: client.chat_postMessage(channel=uid, text="❌ 리마인드 예약 중 오류가 발생했습니다.")