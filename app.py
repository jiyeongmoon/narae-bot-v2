"""
app.py — 나래봇 메인 진입점
================================================
실행 방법:
  로컬: python app.py
  Railway: 자동 실행 (Procfile 참조)
"""

import os
import logging
import datetime
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from flask import Flask
import threading

# 프로세스 기동 시각 (배포 검증용) — 이 시각이 최근이면 새로 재배포된 것
_STARTED_AT = datetime.datetime.now(datetime.timezone.utc).isoformat()

from config import validate_config, SLACK_BOT_TOKEN, SLACK_APP_TOKEN, NARAE_API_KEY, SLACK_REVIEW_USER_ID
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
    # Railway가 배포 시 자동 주입하는 커밋 정보로 "어느 버전이 떠 있는지" 노출
    return {
        "status": "ok",
        "service": "나래봇(SocketMode)",
        "commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown")[:12],
        "branch": os.environ.get("RAILWAY_GIT_BRANCH", "unknown"),
        "started_at": _STARTED_AT,
    }, 200


@flask_app.route("/api/meeting-review", methods=["POST"])
def api_meeting_review():
    """
    meeting-bot → narae-bot Task 검토 요청 엔드포인트.

    Request JSON:
      {
        "session_id": str (선택 — 없으면 UUID 생성),
        "filename":   str,
        "meeting_page_id":  str,
        "meeting_page_url": str,
        "meeting_title":    str,
        "tasks": [
          {
            "업무명": str,
            "담당자": str,
            "우선순위": str,   # P1/P2/P3
            "마감일": str,     # YYYY-MM-DD
            "발주처": str,
            "내용": str,       # 마크다운 체크리스트
            "project_page_id": str,
          },
          ...
        ]
      }

    Response JSON:
      {"ok": true, "session_id": str, "task_count": int}
    """
    import uuid
    from flask import request, jsonify
    from services.cache import set as cache_set
    from services.slack import build_meeting_notification, build_meeting_only_notification

    # ── API 키 인증 ──────────────────────────────────────────────
    if NARAE_API_KEY:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {NARAE_API_KEY}":
            return jsonify({"ok": False, "error": "Unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": False, "error": "JSON body required"}), 400

    session_id      = data.get("session_id") or str(uuid.uuid4())[:12]
    tasks           = data.get("tasks", [])
    filename        = data.get("filename", "")
    meeting_page_id = data.get("meeting_page_id", "")
    meeting_page_url = data.get("meeting_page_url", "")
    meeting_title   = data.get("meeting_title", "")
    loose_todos     = data.get("일정메모", []) or []

    # ── 세션 데이터 캐싱 (TTL: 24h) ─────────────────────────────
    cache_set(f"mtg:{session_id}", {
        "session_id":       session_id,
        "filename":         filename,
        "tasks":            tasks,
        "meeting_page_id":  meeting_page_id,
        "meeting_page_url": meeting_page_url,
        "meeting_title":    meeting_title,
        "loose_todos":      loose_todos,
    }, ttl=86400)

    logging.info(f"[meeting-review] 세션 캐싱 완료: {session_id} ({len(tasks)}건, {filename})")

    # ── Slack 알림 발송 (검토 담당자에게 DM) ────────────────────
    review_target = SLACK_REVIEW_USER_ID
    if review_target:
        try:
            if tasks:
                blocks = build_meeting_notification(session_id, filename, len(tasks))
                text = f"📋 회의록 Task 검토 요청: {filename} ({len(tasks)}건)"
            else:
                # Task가 0건이어도 회의록만 등록됐음을 알림 (검토 버튼 없음)
                blocks = build_meeting_only_notification(filename, meeting_title, meeting_page_url)
                text = f"📋 회의록 등록 완료 (Task 없음): {filename}"
            bolt_app.client.chat_postMessage(
                channel=review_target,
                text=text,
                blocks=blocks,
            )
            logging.info(f"[meeting-review] Slack 알림 발송 → {review_target} (tasks={len(tasks)})")
        except Exception as e:
            logging.error(f"[meeting-review] Slack 알림 실패: {e}")

    return jsonify({"ok": True, "session_id": session_id, "task_count": len(tasks)})

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
