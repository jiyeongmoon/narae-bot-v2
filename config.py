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

# ── 드롭박스 (Dropbox) ──────────────────────────────────────────
DROPBOX_APP_KEY = os.environ.get("DROPBOX_APP_KEY")
DROPBOX_APP_SECRET = os.environ.get("DROPBOX_APP_SECRET")
DROPBOX_REFRESH_TOKEN = os.environ.get("DROPBOX_REFRESH_TOKEN")

# 사업기획 파이프라인 프로젝트 기본 경로
# (로컬 PC: 실제 Dropbox 경로 / Railway(클라우드): 더미값 → _get_project_list()가 빈 목록 반환)
PROPOSAL_BASE_DIR = os.environ.get(
    "PROPOSAL_BASE_DIR",
    r"c:\Users\user\공간환경계획연구실 Dropbox\04_Knowledge_Base\00_Obsidian\moon\01_프로젝트_실무_산출물"
)

# ── 프로젝트 폴더 체계 (SOP v2.4) ──────────────────────────────
# 사업팀 표준 7대 상위 폴더 (Active Project - 하위 폴더 없음)
NARE_STANDARD_FOLDERS = [
    "01_기초조사 및 현황분석",
    "02_기본구상 및 전략",
    "03_계획수립 및 설계",
    "04_협의 및 심의",
    "05_최종성과물",
    "06_회의록 및 보고",
    "07_참고 및 기초자료",
    "99_Temp"
]

# 제안서/입찰팀 표준 하위 폴더 (Sales & Proposals)
NARE_PROPOSAL_FOLDERS = [
    "01_공고및RFP",
    "02_제안서작업",
    "03_가격입찰",
    "04_제출및발표",
    "05_결과관리",
    "99_Temp"
]

# 경영지원팀 표준 4대 하위 폴더 (용역행정)
NARE_ADMIN_FOLDERS = [
    "01_계약", "02_착수", "03_예산및인력", "04_완료", "99_Temp"
]

# 분야별 코드 매핑
BIZ_CODE_DISPLAY = {
    "UR": "UR (도시재생 전략·활성화계획)",
    "PL": "PL (도시계획 - 지구단위/관리/정비계획)",
    "RG": "RG (지역개발 - 농촌재구조화/협약/기초생활거점)",
    "CS": "CS (컨설팅 - 모니터링/성과평가/변경용역)",
    "RD": "RD (연구개발 - 학술연구용역)",
    "OP": "OP (운영/거버넌스 - 현장지원/역량강화)",
    "PS": "PS (제안서/입찰 - Sales & Proposals)",
    "C":  "C (용역 행정 관리 - Contract Admin)"
}

# 분야별 상위 폴더 경로 (Dropbox 내 기준)
CATEGORY_MAP = {
    "UR": "02_Active_Project/01_도시재생 (UR)",
    "PL": "02_Active_Project/02_도시계획 (PL)",
    "RG": "02_Active_Project/03_지역개발 (RG)",
    "CS": "02_Active_Project/04_도시 및 지역 컨설팅 (CS)",
    "RD": "02_Active_Project/05_연구개발 (RD)",
    "OP": "02_Active_Project/06_역량강화 (OP)",
    "PS": "03_Sales_Proposals",
    "C":  "01_Management/05_용역행정_Admin"
}

FOLDER_STRUCTURES = {
    "C": NARE_ADMIN_FOLDERS,
    "PS": NARE_PROPOSAL_FOLDERS,
    # 나머지는 모두 사업팀 표준 구조 적용
    **{k: NARE_STANDARD_FOLDERS for k in BIZ_CODE_DISPLAY.keys() if k not in ["C", "PS"]}
}

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
