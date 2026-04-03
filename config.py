"""
config.py — 환경변수 및 상수 관리
===================================
.env 파일 또는 Railway 환경변수에서 값을 읽습니다.

[설정 방법]
로컬 개발: 프로젝트 루트에 .env 파일 생성 후 아래 항목 입력
Railway 배포: 대시보드 > Variables 에 동일 항목 입력
"""

import os
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

# ── 슬랙 ──────────────────────────────────────────────────────
# 슬랙 앱 생성 후 발급
# api.slack.com > 앱 선택 > OAuth & Permissions > Bot User OAuth Token
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")

# api.slack.com > 앱 선택 > Basic Information > Signing Secret
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET")

# api.slack.com > 앱 선택 > Basic Information > App-Level Tokens (xapp-...)
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")

# ── 노션 ──────────────────────────────────────────────────────
# notion.so > Settings > Integrations > 새 integration 생성 후 발급
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")

# TASK DB 노션 페이지 URL 에서 복사
# URL 형식: notion.so/workspace/[여기가-DB-ID]?v=...
# 하이픈 없이 32자리 문자열
NOTION_TASK_DB_ID = os.environ.get("NOTION_TASK_DB_ID")

# 인원 DB (사내 인적 자원 정보 매칭용)
NOTION_USER_DB_ID = os.environ.get("NOTION_USER_DB_ID", "")

# 일지 기록용 DB (Phase 2에서 사용, 선택사항)
NOTION_LOG_DB_ID = os.environ.get("NOTION_LOG_DB_ID", "")

# ── 슬랙 채널 ─────────────────────────────────────────────────
# 매일 17시 일지 작성 알림을 보낼 채널 ID
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID")

# 일지 전용 채널 ID (메시지 입력 시 자동으로 노션에 기록)
SLACK_LOG_CHANNEL_ID = os.environ.get("SLACK_LOG_CHANNEL_ID")

# 대표 슬랙 사용자 ID (/kpi 명령어 접근 제한용, 선택)
SLACK_ADMIN_ID = os.environ.get("SLACK_ADMIN_ID", "")

# ── 유효성 검사 ───────────────────────────────────────────────
def validate_config():
    """앱 시작 시 필수 환경변수가 모두 있는지 확인합니다."""
    required = {
        "SLACK_BOT_TOKEN": SLACK_BOT_TOKEN,
        "SLACK_APP_TOKEN": SLACK_APP_TOKEN,
        "SLACK_SIGNING_SECRET": SLACK_SIGNING_SECRET,
        "NOTION_TOKEN": NOTION_TOKEN,
        "NOTION_TASK_DB_ID": NOTION_TASK_DB_ID,
        "NOTION_USER_DB_ID": NOTION_USER_DB_ID,
        "SLACK_CHANNEL_ID": SLACK_CHANNEL_ID,
        "SLACK_LOG_CHANNEL_ID": SLACK_LOG_CHANNEL_ID,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise EnvironmentError(
            f"❌ 환경변수 누락: {', '.join(missing)}\n"
            f".env 파일 또는 Railway Variables 확인 필요"
        )
    logger.info("환경변수 확인 완료")
