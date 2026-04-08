import logging
import os
from slack_bolt import App
from config import SLACK_BOT_TOKEN, SLACK_CHANNEL_ID
from services.slack import build_daily_reminder_message

logging.basicConfig(level=logging.INFO)
app = App(token=SLACK_BOT_TOKEN)

def test_send():
    print(f"DEBUG: TARGET CHANNEL={SLACK_CHANNEL_ID}")
    try:
        res = app.client.chat_postMessage(
            channel=SLACK_CHANNEL_ID,
            text="[테스트] 오늘의 업무일지를 작성해 주세요.",
            blocks=build_daily_reminder_message()
        )
        print(f"SUCCESS: {res['ok']}")
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    test_send()
