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


def send_daily_reminder(slack_client):
    """채널에 일지 작성 알림 메시지를 전송합니다."""
    try:
        slack_client.chat_postMessage(
            channel=SLACK_CHANNEL_ID,
            text="오늘의 업무일지를 작성해 주세요.",
            blocks=build_daily_reminder_message(),
        )
        logger.info("일지 작성 알림 전송 완료")
    except Exception as e:
        logger.error(f"일지 작성 알림 전송 실패: {e}")


def send_weekly_summary(slack_client):
    """금요일 18:00 주간 요약을 채널에 전송합니다."""
    from services.notion import get_weekly_updated_tasks

    try:
        tasks = get_weekly_updated_tasks()
        blocks = build_weekly_summary_message(tasks)
        slack_client.chat_postMessage(
            channel=SLACK_CHANNEL_ID,
            text="📊 주간 요약",
            blocks=blocks,
        )
        logger.info(f"주간 요약 전송 완료 ({len(tasks)}개 Task)")
    except Exception as e:
        logger.error(f"주간 요약 전송 실패: {e}")


def start_scheduler(slack_client):
    """APScheduler 초기화 및 시작."""
    scheduler = BackgroundScheduler()
    scheduler.add_job(
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
    scheduler.add_job(
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
    scheduler.start()
    logger.info("스케줄러 시작 — 평일 17:00 일지 알림, 금 18:00 주간 요약 예약됨")
