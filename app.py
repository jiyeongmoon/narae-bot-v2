"""
app.py — 나래봇 메인 진입점
================================================
실행 방법:
  로컬: python app.py
  Railway: 자동 실행 (Procfile 참조)
"""

import os
import logging
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request

from config import validate_config, SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET
from handlers.command import register_commands
from handlers.action import register_actions
from handlers.modal import register_modals
from handlers.options import register_options
from handlers.message import register_messages
from services.scheduler import start_scheduler
from services.notion import ensure_db_properties, ensure_log_db

# ── 로깅 설정 ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

# ── 환경변수 검증 ─────────────────────────────────────────────
validate_config()

# ── 노션 DB 속성 확인 (발주처/현재단계) ───────────────────────
ensure_db_properties()

# ── 일지 DB 확인/생성 (Phase 3) ──────────────────────────────
log_db_id = ensure_log_db()
if log_db_id:
    logging.info(f"일지 DB 연결: {log_db_id}")

# ── 슬랙 앱 초기화 ────────────────────────────────────────────
bolt_app = App(
    token=SLACK_BOT_TOKEN,
    signing_secret=SLACK_SIGNING_SECRET,
)

# ── 핸들러 등록 ───────────────────────────────────────────────
register_commands(bolt_app)
register_actions(bolt_app)
register_modals(bolt_app)
register_options(bolt_app)
register_messages(bolt_app)

# ── 스케줄러 시작 ──────────────────────────────────────────────
start_scheduler(bolt_app.client)

# ── Flask 서버 (Railway 배포용) ───────────────────────────────
flask_app = Flask(__name__)
handler = SlackRequestHandler(bolt_app)

@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

@flask_app.route("/health", methods=["GET"])
def health():
    """Railway 헬스체크 엔드포인트."""
    return {"status": "ok", "service": "나래봇"}, 200

# ── 실행 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    logging.info(f"나래봇 시작 (port={port})")
    flask_app.run(host="0.0.0.0", port=port, debug=False)
