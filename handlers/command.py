"""
handlers/command.py — 슬랙 슬래시 커맨드 처리
==============================================
- /일지: Task 선택 모달
- /인수인계: 인수인계 초안 생성
"""

from services.notion import get_all_tasks
from services.slack import (
    build_task_select_modal,
    build_handover_select_modal,
    build_deadline_risk_message,
    build_error_message,
)


def register_commands(app):

    @app.command("/일지")
    def handle_ilji_command(ack, body, client, logger):
        ack()

        user_id = body["user_id"]
        user_name = body["user_name"]

        logger.info(f"/일지 요청: {user_name} ({user_id})")

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
            try:
                user_info = client.users_info(user=user_id)
                real_name = user_info["user"]["profile"].get("real_name", "")
            except Exception:
                real_name = ""

            from services.notion import get_all_tasks, get_my_tasks
            
            if real_name:
                # get_my_tasks가 페이징 대응 및 전체 조회를 수행하며, 담당 업무를 최상단으로 정렬합니다.
                tasks = get_my_tasks(real_name)
            else:
                tasks = get_all_tasks()
                for t in tasks:
                    t.setdefault("is_assigned", False)

            logger.info(f"/일지 명령어 — Task {len(tasks)}개 구성 (assigned={sum(1 for t in tasks if t.get('is_assigned'))}, 사용자: {real_name})")

            modal = build_task_select_modal(tasks, user_real_name=real_name)
            client.views_update(view_id=view_id, view=modal)

        except Exception as e:
            logger.error(f"/일지 처리 오류: {e}")
            client.chat_postMessage(
                channel=user_id,
                blocks=build_error_message(str(e))
            )

    @app.command("/인수인계")
    def handle_handover_command(ack, body, client, logger):
        ack()

        user_id = body["user_id"]
        logger.info(f"/인수인계 요청: {user_id}")

        try:
            loading_view = {
                "type": "modal",
                "title": {"type": "plain_text", "text": "📋 인수인계 초안"},
                "close": {"type": "plain_text", "text": "취소"},
                "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "⏳ Task 목록을 불러오는 중..."}}],
            }
            resp = client.views_open(
                trigger_id=body["trigger_id"],
                view=loading_view,
            )
            view_id = resp["view"]["id"]

            tasks = get_all_tasks()
            logger.info(f"인수인계 Task 조회: {len(tasks)}개")

            modal = build_handover_select_modal(tasks)
            client.views_update(view_id=view_id, view=modal)

        except Exception as e:
            logger.error(f"/인수인계 처리 오류: {e}")
            client.chat_postMessage(
                channel=user_id,
                blocks=build_error_message(str(e))
            )

    @app.command("/스케줄확인")
    def handle_check_schedule_command(ack, body, client, logger):
        """현재 예약된 작업 현황 조회"""
        ack()
        user_id = body["user_id"]
        logger.info(f"/스케줄확인 요청: {user_id}")
        
        from services.scheduler import get_scheduler_info
        info = get_scheduler_info()
        client.chat_postEphemeral(channel=body["channel_id"], user=user_id, text=f"🕒 {info}")

    @app.command("/마감리스크")
    def handle_deadline_risk_command(ack, body, client, respond, logger):
        """현재 마감리스크가 체크된 업무 현황을 즉시 조회합니다. (타임아웃 방지를 위해 비동기 처리)"""
        ack()  # 슬랙에 즉시 응답 반환 (3초 제한 회피)
        user_id = body["user_id"]
        logger.info(f"/마감리스크 요청 (비동기): {user_id}")

        def _fetch_and_respond():
            try:
                from services.notion import get_deadline_risk_tasks
                tasks = get_deadline_risk_tasks()
                if not tasks:
                    respond(text="✅ 현재 감지된 마감리스크 업무가 없습니다.")
                    return

                blocks = build_deadline_risk_message(tasks)
                respond(
                    text="🚨 마감리스크 업무 현황",
                    blocks=blocks,
                    replace_original=False
                )
            except Exception as e:
                logger.error(f"/마감리스크 백그라운드 작업 오류: {e}")
                respond(text=f"❌ 조회 중 오류가 발생했습니다: {e}")

        import threading
        threading.Thread(target=_fetch_and_respond).start()

