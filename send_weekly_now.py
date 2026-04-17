"""
send_weekly_now.py — 주간 요약 즉시 발송 스크립트
==============================================
스케줄러를 기다리지 않고 지금 바로 주간 요약을 채널에 전송합니다.
"""
import logging
import sys
import os

# 나래봇 루트를 경로에 추가
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

from config import SLACK_BOT_TOKEN, SLACK_CHANNEL_ID
from slack_sdk import WebClient
from services.notion import get_weekly_updated_tasks
from services.slack import build_weekly_summary_message

client = WebClient(token=SLACK_BOT_TOKEN)

tasks = get_weekly_updated_tasks(only_assigned=True)
if not tasks:
    print("⚠️  이번 주 담당자 지정된 업데이트 Task가 없습니다. 발송을 생략합니다.")
    sys.exit(0)

blocks = build_weekly_summary_message(tasks)
res = client.chat_postMessage(
    channel=SLACK_CHANNEL_ID,
    text="📊 주간 요약",
    blocks=blocks,
)
print(f"✅ 주간 요약 발송 완료 (ts: {res['ts']}, {len(tasks)}개 Task 기반)")
