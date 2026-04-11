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
from slack_bolt.adapter.socket_mode import SocketModeHandler
from flask import Flask
import threading

from config import validate_config, SLACK_BOT_TOKEN, SLACK_APP_TOKEN
from handlers.command import register_commands
from handlers.action import register_actions
from handlers.modal import register_modals
from handlers.options import register_options
from handlers.message import register_messages
from handlers.project import register_project_handlers
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
bolt_app = App(token=SLACK_BOT_TOKEN)

# ── 핸들러 등록 ───────────────────────────────────────────────
register_commands(bolt_app)
register_actions(bolt_app)
register_modals(bolt_app)
register_options(bolt_app)
register_messages(bolt_app)
register_project_handlers(bolt_app)

# ── 스케줄러 시작 ──────────────────────────────────────────────
start_scheduler(bolt_app.client)

# ── Flask 서버 (Railway 헬스체크용) ───────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "service": "나래봇(SocketMode)"}, 200

@flask_app.route("/launch", methods=["GET"])
def launch_proposal():
    html_content = '''
    <html>
        <head>
            <title>나래 제안서 시스템 런처</title>
            <meta charset="utf-8">
        </head>
        <body style="font-family: 'Pretendard', sans-serif; text-align: center; padding: 100px; background: #f8fafc;">
            <h1 style="color: #0f172a; font-size: 24px; margin-bottom: 20px;">🚀 로컬 제안서 시스템 연결</h1>
            <p style="color: #475569; font-size: 16px; margin-bottom: 40px;">
                브라우저 보안 정책으로 인해 자동 실행이 차단되었을 수 있습니다.<br>
                아래 버튼을 한 번 더 클릭하여 시스템을 실행해 주세요.
            </p>
            <a href="narae-proposal://launch" 
               style="padding: 15px 40px; background-color: #2563eb; color: #ffffff; text-decoration: none; border-radius: 8px; font-weight: 800; font-size: 18px; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);">
               🖥️ 시스템 직접 켜기
            </a>
            <script>
                // 접속 즉시 한 번 시도
                window.location.href = "narae-proposal://launch";
            </script>
        </body>
    </html>
    '''
    return html_content, 200

def run_flask():
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port, debug=False)

# ── 실행 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.info("나래봇 소켓 모드 기동 중...")
    
    # 1. 헬스체크 서버를 별도 스레드에서 실행
    threading.Thread(target=run_flask, daemon=True).start()
    
    # 2. 소켓 모드 핸들러 시작
    handler = SocketModeHandler(bolt_app, SLACK_APP_TOKEN)
    handler.start()
