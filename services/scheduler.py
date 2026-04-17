"""
services/scheduler.py — 스케줄러 (일지 알림 + 주간 요약)
========================================================
- 평일(월~금) 17:00 KST: 일지 작성 알림
- 금요일 18:00 KST: 주간 요약 자동 발송
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import SLACK_CHANNEL_ID
from services.slack import build_daily_reminder_message, build_weekly_summary_message

logger = logging.getLogger(__name__)


# 전역 스케줄러 인스턴스
_scheduler = None

def send_daily_reminder(slack_client):
    """채널에 일지 작성 알림 메시지를 전송합니다."""
    try:
        from config import SLACK_CHANNEL_ID
        res = slack_client.chat_postMessage(
            channel=SLACK_CHANNEL_ID,
            text="오늘의 업무일지를 작성해 주세요.",
            blocks=build_daily_reminder_message(),
        )
        logger.info(f"일지 작성 알림 전송 성공: {SLACK_CHANNEL_ID} (ts: {res.get('ts')})")
        return True
    except Exception as e:
        logger.error(f"일지 작성 알림 전송 실패: {e}")
        return False


def send_weekly_summary(slack_client):
    """금요일 18:00 주간 요약을 채널에 전송합니다."""
    from services.notion import get_weekly_updated_tasks
    from config import SLACK_CHANNEL_ID

    try:
        tasks = get_weekly_updated_tasks(only_assigned=True)

        # ── 데이터 품질 검증: 정상 업무가 없으면 조용히 생략 ──────────
        if not tasks:
            logger.warning(
                "주간 요약 발송 생략: 이번 주 담당자가 지정된 업데이트 Task가 0건입니다."
            )
            return

        blocks = build_weekly_summary_message(tasks)
        slack_client.chat_postMessage(
            channel=SLACK_CHANNEL_ID,
            text="📊 주간 요약",
            blocks=blocks,
        )
        logger.info(f"주간 요약 전송 완료 ({len(tasks)}개 Task 기반)")
    except Exception as e:
        logger.error(f"주간 요약 전송 실패: {e}")


def send_deadline_risk_alert(slack_client):
    """채널에 마감리스크 업무 알림 메시지를 전송합니다."""
    from services.notion import get_deadline_risk_tasks
    from services.slack import build_deadline_risk_message
    from config import SLACK_CHANNEL_ID

    try:
        tasks = get_deadline_risk_tasks()
        if not tasks:
            logger.info("마감리스크 항목 없음 (알림 미발송)")
            return

        blocks = build_deadline_risk_message(tasks)
        slack_client.chat_postMessage(
            channel=SLACK_CHANNEL_ID,
            text="🚨 마감리스크 업무 알림",
            blocks=blocks,
        )
        logger.info(f"마감리스크 알림 전송 완료 ({len(tasks)}건)")
    except Exception as e:
        logger.error(f"마감리스크 알림 전송 실패: {e}")


def get_scheduler_info():
    """현재 스케줄러의 예약 상태를 요약하여 반환합니다."""
    if not _scheduler:
        return "스케줄러가 초기화되지 않았습니다."
    
    jobs = _scheduler.get_jobs()
    if not jobs:
        return "등록된 예약 작업이 없습니다."
    
    info = "[현재 예약 현황]\n"
    for job in jobs:
        next_run = job.next_run_time.strftime('%Y-%m-%d %H:%M:%S') if job.next_run_time else "없음"
        info += f"- {job.id}: 다음 실행 {next_run} (KST)\n"
    return info


def start_scheduler(slack_client):
    """APScheduler 초기화 및 시작."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        send_daily_reminder,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=17,
            minute=0,
            timezone="Asia/Seoul",
        ),
        args=[slack_client],
        id="daily_ilji_reminder",
    )
    _scheduler.add_job(
        send_weekly_summary,
        trigger=CronTrigger(
            day_of_week="fri",
            hour=18,
            minute=0,
            timezone="Asia/Seoul",
        ),
        args=[slack_client],
        id="weekly_summary",
    )
    _scheduler.add_job(
        send_deadline_risk_alert,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=9,
            minute=0,
            timezone="Asia/Seoul",
        ),
        args=[slack_client],
        id="deadline_risk_alert",
    )
    _scheduler.start()
    
    for job in _scheduler.get_jobs():
        logger.info(f"작업 예약됨: {job.id} -> {job.next_run_time} (KST)")
    
    logger.info("스케줄러 시작 완료")
