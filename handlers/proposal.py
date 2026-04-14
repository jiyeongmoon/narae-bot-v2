"""
handlers/proposal.py
====================
/사업기획초안 슬랙 커맨드 처리.

[아키텍처]
- Railway(Slack 봇)이 커맨드를 받아 Dropbox에 트리거 JSON 업로드
- 로컬 local_watcher.py가 트리거 파일을 감지하여 파이프라인 실행
- 이렇게 Railway-로컬 PC 간 파일시스템 충돌 회피
"""

import os
import sys
import json
import threading
from datetime import datetime

# ── proposal_system 경로 동적 추가 ─────────────────────────────────────
_NARAE_BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROPOSAL_SYSTEM_DIR = os.path.normpath(
    os.path.join(_NARAE_BOT_DIR,
                 "..", "..", "02_데이터_및_스크립트", "02_자동화_스크립트", "proposal_system")
)
if _PROPOSAL_SYSTEM_DIR not in sys.path:
    sys.path.insert(0, _PROPOSAL_SYSTEM_DIR)

# ── 안전한 import: 실패해도 narae-bot 전체가 죽지 않음 ─────────────────
_PIPELINE_AVAILABLE = False
PROPOSAL_BASE_DIR = r"c:\Users\user\공간환경계획연구실 Dropbox\04_Knowledge_Base\00_Obsidian\moon\01_프로젝트_실무_산출물"
DROPBOX_APP_KEY = DROPBOX_APP_SECRET = DROPBOX_REFRESH_TOKEN = None
try:
    from config import PROPOSAL_BASE_DIR as _pb
    from config import DROPBOX_APP_KEY, DROPBOX_APP_SECRET, DROPBOX_REFRESH_TOKEN, PROPOSAL_ROOTS
    PROPOSAL_BASE_DIR = _pb
    _PIPELINE_AVAILABLE = True
except Exception as _import_err:
    import logging as _log
    _log.warning(f"[proposal] 모듈 로드 실패 → /사업기획초안 비활성화: {_import_err}")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash-exp")
CALLBACK_ID_PROPOSAL = "planning_draft_modal"

# Dropbox 트리거 저장 경로 (Dropbox API 내 절대 경로)
TRIGGER_DROPBOX_PATH = "/04_Knowledge_Base/00_Obsidian/moon/02_데이터_및_스크립트/02_자동화_스크립트/proposal_system/_triggers"


# ── 프로젝트 목록 조회 ─────────────────────────────────────────────────

def _get_project_list(root_key: str = "01_프로젝트_실무_산출물") -> list[dict]:
    """선택한 루트 폴더의 프로젝트 목록 반환. 로컬 파일시스템 → Dropbox API 순으로 시도."""
    root_info = PROPOSAL_ROOTS.get(root_key, PROPOSAL_ROOTS["01_프로젝트_실무_산출물"])
    local_path = root_info["local"]
    dropbox_path = root_info["dropbox"]

    # 1차: 로컬 파일시스템 (로컬 PC에서 실행 시)
    if os.path.exists(local_path):
        dirs = sorted(
            [d for d in os.listdir(local_path)
             if os.path.isdir(os.path.join(local_path, d))],
            reverse=True
        )
        return [{"text": {"type": "plain_text", "text": d[:75]}, "value": d} for d in dirs[:100]]

    # 2차: Dropbox API 폴백 (Railway 클라우드에서 실행 시)
    if not DROPBOX_APP_KEY or not DROPBOX_REFRESH_TOKEN:
        return []
    try:
        import dropbox as dbx_lib
        dbx = dbx_lib.Dropbox(
            app_key=DROPBOX_APP_KEY,
            app_secret=DROPBOX_APP_SECRET,
            oauth2_refresh_token=DROPBOX_REFRESH_TOKEN
        )
        try:
            account_info = dbx.users_get_current_account()
            root_ns = account_info.root_info.root_namespace_id
            dbx = dbx.with_path_root(dbx_lib.common.PathRoot.root(root_ns))
        except Exception:
            pass

        res = dbx.files_list_folder(dropbox_path)
        dirs = sorted(
            [e.name for e in res.entries
             if isinstance(e, dbx_lib.files.FolderMetadata)],
            reverse=True
        )
        return [{"text": {"type": "plain_text", "text": d[:75]}, "value": d} for d in dirs[:100]]
    except Exception as e:
        import logging
        logging.warning(f"[proposal] Dropbox 프로젝트 목록 조회 실패 ({root_key}): {e}")
        return []


# ── 모달 빌드 ──────────────────────────────────────────────────────────

def _build_proposal_modal(project_list: list[dict], selected_root: str = "01_프로젝트_실무_산출물") -> dict:
    """제안서 초안 생성 Slack 모달 빌드"""

    MODEL_OPTIONS = [
        {"text": {"type": "plain_text", "text": "gemini-2.0-flash-exp"},  "value": "gemini-2.0-flash-exp"},
        {"text": {"type": "plain_text", "text": "gemini-1.5-pro"},        "value": "gemini-1.5-pro"},
        {"text": {"type": "plain_text", "text": "gemini-1.5-flash"},      "value": "gemini-1.5-flash"},
    ]
    default_model  = GEMINI_MODEL or "gemini-2.0-flash-exp"
    default_option = next(
        (o for o in MODEL_OPTIONS if o["value"] == default_model), MODEL_OPTIONS[0]
    )

    # 루트 카테고리 옵션 생성
    ROOT_OPTIONS = [
        {"text": {"type": "plain_text", "text": info["name"]}, "value": key}
        for key, info in PROPOSAL_ROOTS.items()
    ]
    current_root_option = next(
        (o for o in ROOT_OPTIONS if o["value"] == selected_root), ROOT_OPTIONS[0]
    )

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "🏢 *사업기획 초안을 자동 생성합니다.*\n카테고리와 프로젝트를 선택하고 기획 방향을 입력하세요."}
        },
        {"type": "divider"},
        {
            "type": "input",
            "block_id": "root_block",
            "label": {"type": "plain_text", "text": "📂 루트 카테고리 선택"},
            "element": {
                "type": "static_select",
                "action_id": "root_category_select",
                "initial_option": current_root_option,
                "options": ROOT_OPTIONS
            }
        },
        {
            "type": "input",
            "block_id": "project_block",
            "label": {"type": "plain_text", "text": "📁 프로젝트 폴더 선택"},
            "element": {
                "type": "static_select",
                "action_id": "project_select",
                "placeholder": {"type": "plain_text", "text": "프로젝트 폴더를 선택하세요"},
                "options": project_list if project_list else [
                    {"text": {"type": "plain_text", "text": "(프로젝트 없음)"}, "value": "_none"}
                ]
            }
        },
        {
            "type": "input",
            "block_id": "model_block",
            "label": {"type": "plain_text", "text": "🤖 AI 모델 선택"},
            "element": {
                "type": "static_select",
                "action_id": "model_select",
                "initial_option": default_option,
                "options": MODEL_OPTIONS
            }
        },
        {
            "type": "input",
            "block_id": "context_block",
            "label": {"type": "plain_text", "text": "✍️ 추가 기획 컨텍스트 (선택)"},
            "hint": {
                "type": "plain_text",
                "text": "06_회의록 폴더의 최신 파일은 항상 자동 참조됩니다. 여기에는 회의록 외 추가로 강조할 기획 의도나 전략 방향만 입력하세요."
            },
            "element": {
                "type": "plain_text_input",
                "action_id": "context_input",
                "multiline": True,
                "placeholder": {
                    "type": "plain_text",
                    "text": "예: 거점시설 중심 재생, 청년 유입 전략 강조...\n(비워도 무방 — 회의록이 자동으로 포함됩니다)"
                },
                "min_length": 0
            },
            "optional": True
        },
        {
            "type": "input",
            "block_id": "api_key_block",
            "label": {"type": "plain_text", "text": "🔑 Gemini API Key (선택 - 없으면 환경변수 사용)"},
            "element": {
                "type": "plain_text_input",
                "action_id": "api_key_input",
                "placeholder": {"type": "plain_text", "text": "AIza... (비워두면 .env의 GEMINI_API_KEY 사용)"}
            },
            "optional": True
        },
    ]

    return {
        "type": "modal",
        "callback_id": CALLBACK_ID_PROPOSAL,
        "title": {"type": "plain_text", "text": "🚀 사업기획 초안 생성"},
        "submit": {"type": "plain_text", "text": "생성 시작"},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": blocks
    }


# ── Dropbox 트리거 업로드 ──────────────────────────────────────────────

def _upload_trigger_to_dropbox(trigger_data: dict) -> bool:
    """Dropbox에 트리거 JSON 파일 업로드 (로컬 watcher가 감지 후 파이프라인 실행)"""
    if not DROPBOX_APP_KEY or not DROPBOX_REFRESH_TOKEN:
        import logging
        logging.error("[proposal] Dropbox 자격증명 없음. .env에 DROPBOX_APP_KEY 등을 설정하세요.")
        return False
    try:
        import dropbox as dbx_lib
        dbx = dbx_lib.Dropbox(
            app_key=DROPBOX_APP_KEY,
            app_secret=DROPBOX_APP_SECRET,
            oauth2_refresh_token=DROPBOX_REFRESH_TOKEN
        )
        # 팀 Business 계정 대응
        try:
            account_info = dbx.users_get_current_account()
            root_ns = account_info.root_info.root_namespace_id
            dbx = dbx.with_path_root(dbx_lib.common.PathRoot.root(root_ns))
        except Exception:
            pass

        fname = "trigger_{}.json".format(
            trigger_data["timestamp"].replace(":", "-").replace(" ", "_")
        )
        path    = f"{TRIGGER_DROPBOX_PATH}/{fname}"
        content = json.dumps(trigger_data, ensure_ascii=False, indent=2).encode("utf-8")
        dbx.files_upload(content, path, mode=dbx_lib.files.WriteMode.overwrite)
        return True
    except Exception as e:
        import logging
        logging.error(f"[proposal] Dropbox 트리거 업로드 실패: {e}")
        return False


def _send_trigger(user_id: str, project_name: str, user_context: str,
                  api_key: str, model_name: str, root_key: str, client):
    """백그라운드: Dropbox에 트리거 업로드 후 사용자에게 접수 메시지 전송"""
    effective_model = model_name or GEMINI_MODEL
    root_info = PROPOSAL_ROOTS.get(root_key, PROPOSAL_ROOTS["01_프로젝트_실무_산출물"])

    def notify(msg: str):
        try:
            client.chat_postMessage(channel=user_id, text=msg)
        except Exception as e:
            print(f"[Slack DM 오류] {e}")

    trigger_data = {
        "timestamp":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user_id":          user_id,
        "project_name":     project_name,
        "base_dir":         root_info["local"],  # 로컬 봇이 사용할 베이스 경로 추가
        "planning_context": user_context,
        "model":            effective_model,
        "api_key":          api_key or GEMINI_API_KEY,
        "status":           "pending"
    }

    ok = _upload_trigger_to_dropbox(trigger_data)
    if ok:
        notify(
            f"🚀 *[{project_name}]* 사업기획 초안 요청이 접수됐습니다!\n"
            f"💻 로컬 PC의 `사업기획_로컬봇_시작.bat`이 실행 중이면 자동으로 파이프라인이 시작됩니다.\n"
            f"⏱️ 완료 시 알림이 옵니다!"
        )
    else:
        notify("❌ Dropbox 트리거 전송 실패. 나래봇 로그를 확인해 주세요.")


# ── 핸들러 등록 ────────────────────────────────────────────────────────

def register_proposal_handlers(app):
    """Slack 앱에 /사업기획초안 커맨드 및 모달 핸들러 등록"""

    @app.command("/사업기획초안")
    def handle_proposal_draft_command(ack, body, client, logger):
        """슬랙에서 사업기획 초안 생성 모달 열기"""
        ack()
        user_id = body["user_id"]
        logger.info(f"/사업기획초안 요청: {user_id}")

        if not _PIPELINE_AVAILABLE:
            client.chat_postMessage(
                channel=user_id,
                text="⚠️ 제안서 파이프라인 모듈이 로드되지 않았습니다.\n나래봇 로그를 확인하거나 관리자에게 문의해 주세요."
            )
            return

        try:
            project_list = _get_project_list()
            modal = _build_proposal_modal(project_list)
            client.views_open(trigger_id=body["trigger_id"], view=modal)
        except Exception as e:
            logger.error(f"/사업기획초안 모달 오류: {e}")
            client.chat_postMessage(channel=user_id, text=f"❌ 모달 열기 오류: {e}")

    @app.action("root_category_select")
    def handle_root_category_select(ack, body, client, logger):
        """루트 카테고리 변경 시 프로젝트 목록 동적 갱신"""
        ack()
        view_id = body["view"]["id"]
        selected_root = body["actions"][0]["selected_option"]["value"]
        logger.info(f"루트 카테고리 변경: {selected_root}")

        try:
            project_list = _get_project_list(selected_root)
            modal = _build_proposal_modal(project_list, selected_root=selected_root)
            client.views_update(view_id=view_id, view=modal)
        except Exception as e:
            logger.error(f"모달 갱신 오류: {e}")

    @app.view(CALLBACK_ID_PROPOSAL)
    def handle_proposal_modal_submit(ack, body, client, logger):
        """모달 제출 처리 → Dropbox 트리거 업로드 (백그라운드)"""
        ack()
        user_id = body["user"]["id"]
        values  = body["view"]["state"]["values"]

        root_key       = values.get("root_block",    {}).get("root_category_select", {}).get("selected_option", {}).get("value", "01_프로젝트_실무_산출물")
        project_name   = values.get("project_block", {}).get("project_select", {}).get("selected_option", {}).get("value", "")
        selected_model = values.get("model_block",   {}).get("model_select",   {}).get("selected_option", {}).get("value", "") or GEMINI_MODEL
        user_context   = values.get("context_block", {}).get("context_input",  {}).get("value", "") or ""
        api_key_input  = values.get("api_key_block", {}).get("api_key_input",  {}).get("value", "") or ""

        if not project_name or project_name == "_none":
            client.chat_postMessage(channel=user_id, text="❌ 프로젝트를 선택해주세요.")
            return

        logger.info(f"/사업기획초안 제출: {user_id} → {project_name} (루트: {root_key}) / 모델: {selected_model}")

        threading.Thread(
            target=_send_trigger,
            args=(user_id, project_name, user_context, api_key_input, selected_model, root_key, client),
            daemon=True
        ).start()
