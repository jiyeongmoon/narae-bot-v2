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
                # 내 담당 업무 우선 조회 (is_assigned 필드 포함)
                tasks = get_my_tasks(real_name)
                my_assigned_count = sum(1 for t in tasks if t.get("is_assigned"))
                # 담당 업무가 3개 미만인 경우에만 전체에서 보충
                if my_assigned_count < 3:
                    all_tasks = get_all_tasks()
                    existing_ids = {t["id"] for t in tasks}
                    for t in all_tasks:
                        if t["id"] not in existing_ids:
                            t["is_assigned"] = False  # ← 보충 task에 is_assigned 명시
                            tasks.append(t)
            else:
                tasks = get_all_tasks()
                for t in tasks:
                    t.setdefault("is_assigned", False)  # ← is_assigned 누락 방지

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

    @app.command("/주간요약")
    def handle_weekly_summary_command(ack, body, client, respond, logger):
        ack()

        user_id = body["user_id"]
        logger.info(f"/주간요약 요청: {user_id}")

        try:
            tasks = get_weekly_updated_tasks(only_assigned=True)
            blocks = build_weekly_summary_message(tasks)
            respond(
                text="📊 주간 요약 (담당자 기준)",
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
            tasks = get_weekly_updated_tasks(only_assigned=True)
            blocks = build_weekly_summary_message(tasks)
            respond(
                text="📈 주간 KPI 리포트 (담당자 기준)",
                blocks=blocks,
            )
        except Exception as e:
            logger.error(f"/kpi 처리 오류: {e}")
            respond(text=f"❌ 오류가 발생했습니다: {e}")
    @app.command("/알림테스트")
    def handle_test_reminder_command(ack, body, client, logger):
        """수동으로 5시 알림을 트리거하여 채널 발송 여부 테스트"""
        ack()
        user_id = body["user_id"]
        logger.info(f"/알림테스트 요청: {user_id}")
        
        from services.scheduler import send_daily_reminder
        from config import SLACK_CHANNEL_ID
        
        try:
            # send_daily_reminder 내부에서 발생하는 에러를 직접 잡기 위해 로직을 여기서 재현하거나 
            # 해당 함수가 에러 객체를 반환하도록 수정 (일단 여기서는 직접 호출 시도)
            from services.slack import build_daily_reminder_message
            client.chat_postMessage(
                channel=SLACK_CHANNEL_ID,
                text="[테스트] 오늘의 업무일지를 작성해 주세요.",
                blocks=build_daily_reminder_message()
            )
            msg = f"✅ 알림 전송 성공! (채널 ID: {SLACK_CHANNEL_ID})"
        except Exception as e:
            msg = f"❌ 알림 전송 실패: {str(e)}\n(설정된 채널 ID: {SLACK_CHANNEL_ID})"
        
        client.chat_postEphemeral(channel=body["channel_id"], user=user_id, text=msg)

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

    @app.command("/제안서")
    def handle_proposal_command(ack, body, client, logger):
        """슬랙에서 윈도우 로컬 제안서 시스템을 즉시 호출하는 커스텀 링크(narae-proposal://) 제공"""
        ack()
        user_id = body["user_id"]
        logger.info(f"/제안서 요청: {user_id}")

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "🚀 *제안서 자동화 시스템*\n내 PC에 마련된 전용 제안서 작성 시스템을 호출합니다.\n(⚠️ 최초 1회 `슬랙연동_레지스트리_등록.bat` 실행 필수)"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "🖥️ 통홥 제안서 시스템 실행하기",
                            "emoji": True
                        },
                        "style": "primary",
                        "url": "https://web-production-df45.up.railway.app/launch",
                        "action_id": "launch_proposal_app_btn"
                    }
                ]
            }
        ]

        try:
            client.chat_postMessage(
                channel=user_id,
                text="제안서 시스템 컨트롤",
                blocks=blocks
            )
        except Exception as e:
            logger.error(f"/제안서 에러: {e}")
            client.chat_postMessage(channel=user_id, text=f"오류: {e}")

    @app.action("launch_proposal_app_btn")
    def handle_launch_proposal_app_btn(ack, body, logger):
        """제안서 버튼 클릭 시 발생하는 액션 이벤트를 아무 동작 없이 즉시 ack 처리하여 경고 아이콘(⚠️) 방지"""
        ack()
        logger.info(f"로컬 제안서 시스템 링크 버튼 클릭됨: {body.get('user', {}).get('id')}")

