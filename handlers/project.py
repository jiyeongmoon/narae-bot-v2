import logging
from datetime import datetime
from services.slack import build_error_message
from services.dropbox_service import dropbox_service
from config import BIZ_CODE_DISPLAY

logger = logging.getLogger(__name__)

def register_project_handlers(app):
    """프로젝트 생성 관련 슬랙 핸들러 등록"""

    @app.command("/폴더생성")
    def handle_create_project_cmd(ack, body, client):
        ack()
        # 초기 모달 띄우기
        view = build_project_creation_modal()
        client.views_open(trigger_id=body["trigger_id"], view=view)

    @app.action("project_type_select")
    def handle_type_change(ack, body, client):
        """분야 선택 변경 시 드롭박스를 스캔하여 순번 자동 제안"""
        ack()
        view_id = body["view"]["id"]
        selected_type = body["actions"][0]["selected_option"]["value"]
        
        # 현재 연도 (2자리)
        curr_year = datetime.now().strftime("%y")
        
        # 드롭박스 API를 통해 다음 순번 계산 (약간 느릴 수 있음)
        suggested_id = dropbox_service.get_next_id(selected_type, curr_year)
        
        # 모달 업데이트
        new_view = build_project_creation_modal(
            selected_type=selected_type,
            suggested_id=suggested_id
        )
        client.views_update(view_id=view_id, view=new_view)

    @app.view("project_creation_submit")
    def handle_project_submit(ack, body, client, logger):
        """프로젝트 폴더 생성 프로세스 실행 (비동기)"""
        user_id = body["user"]["id"]
        values = body["view"]["state"]["values"]
        
        # 1. 입력값 추출
        p_type = values["block_type"]["project_type_select"]["selected_option"]["value"]
        p_id = values["block_id"]["project_id_input"]["value"]
        p_name = values["block_name"]["project_name_input"]["value"]
        
        # 2. 필수 값 검증
        if not p_id or not p_name:
            ack(response_action="errors", errors={
                "block_id": "ID를 입력해주세요.",
                "block_name": "프로젝트명을 입력해주세요."
            })
            return
            
        # 3. ACK - 모달 즉시 닫기
        ack(response_action="clear")
        
        # 4. 진행 상태 알림 발송 (업무일지 방식)
        progress_msg = None
        try:
            progress_msg = client.chat_postMessage(
                channel=user_id,
                text=f"⏳ *{p_id}* 프로젝트 폴더를 드롭박스에 생성하고 있습니다... 잠시만 기다려 주세요."
            )
        except Exception as pe:
            logger.error(f"진행 알림 발송 실패: {pe}")

        # 5. 백그라운드 작업 수행 (폴더 생성)
        try:
            success, result = dropbox_service.create_project_folders(p_id, p_name, p_type)
            
            if success:
                path = result["path"]
                link = result["link"]
                
                msg_content = f"✅ *폴더 생성 완료!*\n• *ID*: `{p_id}`\n• *명칭*: {p_name}\n• *경로*: `{path}`"
                if link:
                    msg_content += f"\n<{link}|📎 드롭박스에서 바로 열기>"
                
                # 성공 메시지로 업데이트 또는 신규 발송
                client.chat_postMessage(channel=user_id, text=msg_content)
            else:
                client.chat_postMessage(
                    channel=user_id, 
                    blocks=build_error_message(f"폴더 생성 실패: {result}")
                )
        except Exception as e:
            logger.error(f"폴더 생성 핸들러 오류: {e}")
            client.chat_postMessage(
                channel=user_id, 
                blocks=build_error_message(f"시스템 오류 발생: {str(e)}")
            )
        finally:
            # 진행 알림 삭제
            if progress_msg:
                try:
                    client.chat_delete(channel=user_id, ts=progress_msg["ts"])
                except:
                    pass

def build_project_creation_modal(selected_type=None, suggested_id=""):
    """프로젝트 생성 모달 빌더"""
    options = [
        {"text": {"type": "plain_text", "text": label}, "value": code}
        for code, label in BIZ_CODE_DISPLAY.items()
    ]
    
    initial_option = next((opt for opt in options if opt["value"] == selected_type), None) if selected_type else None
    
    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "✨ *신규 프로젝트/용역 폴더 생성 (SOP v2.4)*\n분야를 선택하면 순번을 자동 계산하며, 드롭박스 클라우드에 표준 폴더가 자동 구축됩니다."}
        },
        {
            "type": "input",
            "block_id": "block_type",
            "dispatch_action": True,
            "element": {
                "type": "static_select",
                "action_id": "project_type_select",
                "placeholder": {"type": "plain_text", "text": "분야를 선택하세요"},
                "options": options,
                **({"initial_option": initial_option} if initial_option else {})
            },
            "label": {"type": "plain_text", "text": "📂 분야 선택 (순번 스캔)"}
        },
        {
            "type": "input",
            "block_id": "block_id",
            "element": {
                "type": "plain_text_input",
                "action_id": "project_id_input",
                "initial_value": suggested_id,
                "placeholder": {"type": "plain_text", "text": "YY-CodeSN (자동 제안됨)"}
            },
            "label": {"type": "plain_text", "text": "🆔 프로젝트 ID"}
        },
        {
            "type": "input",
            "block_id": "block_name",
            "element": {
                "type": "plain_text_input",
                "action_id": "project_name_input",
                "placeholder": {"type": "plain_text", "text": "예: 이천시 장호원읍 도시재생"}
            },
            "label": {"type": "plain_text", "text": "📝 프로젝트/용역 명칭"}
        }
    ]
    
    return {
        "type": "modal",
        "callback_id": "project_creation_submit",
        "title": {"type": "plain_text", "text": "나래공간 폴더 생성"},
        "submit": {"type": "plain_text", "text": "생성하기"},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": blocks
    }
