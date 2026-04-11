import logging
import json
from datetime import datetime
from services.slack import build_error_message
from services.dropbox_service import dropbox_service
from config import BIZ_CODE_DISPLAY, CATEGORY_MAP

logger = logging.getLogger(__name__)

# 루트 폴더 옵션 정의
ROOT_OPTIONS = [
    {"label": "사업 실무 (02_Active_Project)", "value": "02_Active_Project"},
    {"label": "용역 행정 (01_Management)", "value": "01_Management/05_용역행정_Admin"},
    {"label": "제안서/입찰 (03_Sales_Proposals)", "value": "03_Sales_Proposals/01_제안서및입찰_PS"},
]

def register_project_handlers(app):
    """프로젝트 생성 관련 슬랙 핸들러 등록"""

    @app.command("/폴더생성")
    def handle_create_project_cmd(ack, body, client):
        ack()
        # 초기 모달 띄우기 (기본값: 사업 실무)
        view = build_project_creation_modal(selected_root="02_Active_Project")
        client.views_open(trigger_id=body["trigger_id"], view=view)

    @app.action("project_type_select")
    @app.action("project_root_select")
    def handle_modal_updates(ack, body, client):
        """분야 또는 루트 폴더 변경 시 순번 재계산 및 모달 업데이트"""
        ack()
        view_id = body["view"]["id"]
        view_state = body["view"]["state"]["values"]
        
        # 현재 선택된 값들 추출 (Null-safe)
        type_opt = view_state.get("project_type_block", {}).get("project_type_select", {}).get("selected_option")
        selected_type = type_opt.get("value") if type_opt else None
        
        root_opt = view_state.get("project_root_block", {}).get("project_root_select", {}).get("selected_option")
        selected_root = root_opt.get("value") if root_opt else None
                         
        name_input = view_state.get("project_name_block", {}).get("project_name_input", {})
        current_name = name_input.get("value") or ""
        
        # 현재 연도 (2자리)
        curr_year = datetime.now().strftime("%y")
        
        # 드롭박스 API를 통해 다음 순번 계산
        suggested_id = ""
        if selected_type and selected_root:
            suggested_id = dropbox_service.get_next_id(selected_type, curr_year, root_override=selected_root)
        
        # 모달 업데이트
        new_view = build_project_creation_modal(
            selected_type=selected_type,
            selected_root=selected_root,
            suggested_id=suggested_id,
            initial_name=current_name
        )
        client.views_update(view_id=view_id, view=new_view)

    @app.view("project_creation_submit")
    def handle_project_submit(ack, body, client, logger):
        """프로젝트 폴더 생성 프로세스 실행 (비동기)"""
        user_id = body["user"]["id"]
        view_state = body["view"]["state"]["values"]
        meta = json.loads(body["view"].get("private_metadata", "{}"))
        
        # 1. 입력값 추출 (State -> Metadata 순으로 확인)
        def get_val(block: str, action: str, meta_key: str):
            val = view_state.get(block, {}).get(action, {}).get("value")
            return val if val else meta.get(meta_key, "")

        def get_select(block: str, action: str, meta_key: str):
            opt = view_state.get(block, {}).get(action, {}).get("selected_option")
            val = opt["value"] if opt else None
            return val if val else meta.get(meta_key)

        p_type = get_select("project_type_block", "project_type_select", "p_type")
        p_root = get_select("project_root_block", "project_root_select", "p_root")
        p_id = get_val("project_id_block", "project_id_input", "p_id")
        p_name = get_val("project_name_block", "project_name_input", "p_name")
        
        # 2. 필수 값 검증 (p_id, p_name만 체크)
        if not p_id or not p_name:
            ack(response_action="errors", errors={
                "project_id_block": "ID가 인식되지 않았습니다. 입력을 확인해 주세요.",
                "project_name_block": "명칭이 인식되지 않았습니다. 입력을 확인해 주세요."
            })
            return
            
        ack(response_action="clear") # 모달 즉시 닫기
        
        # 3. 진행 상태 알림 발송
        progress_msg = None
        try:
            progress_msg = client.chat_postMessage(
                channel=user_id,
                text=f"⏳ *{p_id}* 폴더를 드롭박스(`{p_root}`)에 생성 중입니다..."
            )
        except Exception as pe:
            logger.error(f"진행 알림 발송 실패: {pe}")

        # 4. 백그라운드 작업 (폴더 생성)
        try:
            success, result = dropbox_service.create_project_folders(p_id, p_name, p_type, root_override=p_root)
            
            if success:
                path = result["path"]
                
                msg_content = f"✅ *폴더 생성 완료!*\n• *ID*: `{p_id}`\n• *명칭*: {p_name}\n• *위치*: `{path}`"
                    
                client.chat_postMessage(channel=user_id, text=msg_content)
            else:
                client.chat_postMessage(channel=user_id, blocks=build_error_message(f"폴더 생성 실패: {result}"))
        except Exception as e:
            logger.error(f"폴더 생성 오류: {e}")
            client.chat_postMessage(channel=user_id, blocks=build_error_message(f"시스템 오류: {str(e)}"))
        finally:
            if progress_msg:
                try: client.chat_delete(channel=user_id, ts=progress_msg["ts"])
                except: pass

def build_project_creation_modal(selected_type=None, selected_root=None, suggested_id="", initial_name=""):
    """프로젝트 생성 모달 빌더 (v2.5)"""
    
    # 저장 위치에 따른 분야 옵션 필터링
    filtered_codes = []
    if selected_root == "01_Management/05_용역행정_Admin":
        filtered_codes = ["C"]
    elif selected_root == "03_Sales_Proposals/01_제안서및입찰_PS":
        filtered_codes = ["PS"]
    else:
        # 기본값 (사업 실무)
        filtered_codes = [k for k in BIZ_CODE_DISPLAY.keys() if k not in ["C", "PS"]]

    # 선택된 분야가 필터링된 배열에 없으면 초기화
    if selected_type not in filtered_codes:
        selected_type = None

    type_options = [
        {"text": {"type": "plain_text", "text": label}, "value": code}
        for code, label in BIZ_CODE_DISPLAY.items() if code in filtered_codes
    ]
    
    root_options = [
        {"text": {"type": "plain_text", "text": opt["label"]}, "value": opt["value"]}
        for opt in ROOT_OPTIONS
    ]
    
    initial_type_opt = next((opt for opt in type_options if opt["value"] == selected_type), None)
    initial_root_opt = next((opt for opt in root_options if opt["value"] == selected_root), None)
    
    # 메타데이터에 현재 상태 저장하여 유실 방지
    metadata = {
        "p_type": selected_type,
        "p_root": selected_root,
        "p_id": suggested_id,
        "p_name": initial_name
    }

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "✨ *나래공간 지능형 폴더 생성 (v2.5)*\n저장 위치와 분야를 선택하면 클라우드에 표준 폴더가 자동 구축됩니다."}
        },
        {
            "type": "divider"
        },
        {
            "type": "input",
            "block_id": "project_root_block",
            "dispatch_action": True,
            "element": {
                "type": "static_select",
                "action_id": "project_root_select",
                "placeholder": {"type": "plain_text", "text": "저장 위치 (Root) 선택"},
                "options": root_options,
                **({"initial_option": initial_root_opt} if initial_root_opt else {})
            },
            "label": {"type": "plain_text", "text": "📍 1. 저장 위치 (최상위)"}
        },
        {
            "type": "input",
            "block_id": "project_type_block",
            "dispatch_action": True,
            "element": {
                "type": "static_select",
                "action_id": "project_type_select",
                "placeholder": {"type": "plain_text", "text": "분야를 선택하세요"},
                "options": type_options,
                **({"initial_option": initial_type_opt} if initial_type_opt else {})
            },
            "label": {"type": "plain_text", "text": "📂 2. 분야 선택 (순번 스캔)"}
        },
        {
            "type": "input",
            "block_id": "project_id_block",
            "element": {
                "type": "plain_text_input",
                "action_id": "project_id_input",
                "initial_value": suggested_id,
                "placeholder": {"type": "plain_text", "text": "YY-CodeSN (자동 제안)"}
            },
            "label": {"type": "plain_text", "text": "🆔 3. 프로젝트 ID"}
        },
        {
            "type": "input",
            "block_id": "project_name_block",
            "element": {
                "type": "plain_text_input",
                "action_id": "project_name_input",
                "initial_value": initial_name,
                "placeholder": {"type": "plain_text", "text": "예: 이천시 장호원읍 도시재생"}
            },
            "label": {"type": "plain_text", "text": "📝 4. 프로젝트/용역 명칭"}
        }
    ]
    
    return {
        "type": "modal",
        "callback_id": "project_creation_submit",
        "private_metadata": json.dumps(metadata),
        "title": {"type": "plain_text", "text": "나래공간 폴더 생성"},
        "submit": {"type": "plain_text", "text": "생성하기"},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": blocks
    }
