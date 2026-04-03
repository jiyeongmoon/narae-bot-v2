"""
handlers/command.py — 슬랙 슬래시 커맨드 처리
==============================================
- /일지: Task 선택 모달
- /인수인계: 인수인계 초안 생성
- /주간요약: 이번 주 업데이트 요약 조회
- /kpi: 대표 전용 KPI 리포트
"""

from config import SLACK_ADMIN_ID
from services.notion import get_all_tasks, get_weekly_updated_tasks
from services.slack import (
    build_task_select_modal,
    build_handover_select_modal,
    build_weekly_summary_message,
    build_kpi_report_message,
    build_error_message,
)


def register_commands(app):

    @app.command("/ilji")
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

            tasks = get_all_tasks()
            logger.info(f"조회된 Task: {len(tasks)}개")

            modal = build_task_select_modal(tasks)
            client.views_update(view_id=view_id, view=modal)

        except Exception as e:
            logger.error(f"/일지 처리 오류: {e}")
            client.chat_postMessage(
                channel=user_id,
                blocks=build_error_message(str(e))
            )

    @app.command("/handover")
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

    @app.command("/weekly")
    def handle_weekly_summary_command(ack, body, client, respond, logger):
        ack()

        user_id = body["user_id"]
        logger.info(f"/주간요약 요청: {user_id}")

        try:
            tasks = get_weekly_updated_tasks()
            blocks = build_weekly_summary_message(tasks)
            respond(
                text="📊 주간 요약",
                blocks=blocks,
                response_type="in_channel",
            )
        except Exception as e:
            logger.error(f"/주간요약 처리 오류: {e}")
            respond(text=f"❌ 오류가 발생했습니다: {e}")

    @app.command("/kpi")
    def handle_kpi_command(ack, body, client, respond, logger):
        ack()

        user_id = body["user_id"]

        if SLACK_ADMIN_ID and user_id != SLACK_ADMIN_ID:
            respond(text="🔒 이 명령어는 대표만 사용할 수 있습니다.")
            return

        logger.info(f"/kpi 요청: {user_id}")

        try:
            tasks = get_weekly_updated_tasks()
            blocks = build_kpi_report_message(tasks)
            # respond 기본값 = ephemeral (본인만 확인)
            respond(
                text="📈 주간 KPI 리포트",
                blocks=blocks,
            )
        except Exception as e:
            logger.error(f"/kpi 처리 오류: {e}")
            respond(text=f"❌ 오류가 발생했습니다: {e}")
