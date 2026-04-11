import logging
import re
from datetime import datetime
import dropbox
from dropbox.exceptions import ApiError
from config import (
    DROPBOX_APP_KEY, DROPBOX_APP_SECRET, DROPBOX_REFRESH_TOKEN,
    CATEGORY_MAP, FOLDER_STRUCTURES
)

logger = logging.getLogger(__name__)

class DropboxService:
    def __init__(self):
        self.app_key = DROPBOX_APP_KEY
        self.app_secret = DROPBOX_APP_SECRET
        self.refresh_token = DROPBOX_REFRESH_TOKEN
        self.dbx = None

    def _get_client(self):
        """Dropbox 클라이언트 인스턴스를 반환 (토큰 자동 갱신 지원)"""
        if self.dbx is None:
            if not self.refresh_token:
                logger.error("Dropbox Refresh Token이 설정되지 않았습니다.")
                return None
            try:
                # Scoped Access 및 Refresh Token을 사용하여 클라이언트 생성
                self.dbx = dropbox.Dropbox(
                    app_key=self.app_key,
                    app_secret=self.app_secret,
                    oauth2_refresh_token=self.refresh_token
                )
                logger.info("Dropbox API 클라이언트 연결 성공")
            except Exception as e:
                logger.error(f"Dropbox 연결 실패: {e}")
                return None
        return self.dbx

    def get_next_id(self, p_type, year_str):
        """드롭박스 폴더를 스캔하여 다음 순번의 ID를 계산합니다."""
        dbx = self._get_client()
        if not dbx: return f"{year_str}-{p_type}01" # 연결 실패 시 01 제안
        
        category_rel_path = CATEGORY_MAP.get(p_type, "")
        if not category_rel_path: return ""
        
        # 스캔 경로 설정
        # v2.4 계층형과 레거시 플랫 경로 모두 확인하기 위해 여러 경로 탐색 가능
        scan_paths = []
        
        # 1. 신규 계층형 경로 (Category/Year 폴더 내부)
        year_folder = f"20{year_str}년"
        if p_type == "PS":
            scan_paths.append(f"/{category_rel_path}/{year_folder}")
        elif p_type == "C":
            scan_paths.append(f"/{category_rel_path}")
        else:
            scan_paths.append(f"/{category_rel_path}/{year_folder}")
            
        # 2. 레거시 경로 (01_프로젝트_실무_산출물)
        scan_paths.append("/01_프로젝트_실무_산출물")
        
        max_sn = 0
        pattern = rf"^{re.escape(year_str)}[\-]{re.escape(p_type)}(\d+)"
        
        for path in scan_paths:
            try:
                res = dbx.files_list_folder(path)
                for entry in res.entries:
                    if isinstance(entry, dropbox.files.FolderMetadata):
                        match = re.search(pattern, entry.name)
                        if match:
                            sn = int(match.group(1))
                            if sn > max_sn: max_sn = sn
            except ApiError as e:
                # 폴더가 없는 경우(신규 연도 등)는 무시
                if e.error.is_path() and e.error.get_path().is_not_found():
                    continue
                logger.warning(f"Dropbox 스캔 실패 ({path}): {e}")
        
        next_sn = max_sn + 1
        return f"{year_str}-{p_type}{next_sn:02d}"

    def create_project_folders(self, p_id, p_name, p_type):
        """SOP v2.4에 맞춰 드롭박스 클라우드에 폴더를 생성합니다."""
        dbx = self._get_client()
        if not dbx: return False, "드롭박스 연결 실패"
        
        # 1. 경로 결정
        year_match = re.search(r"^(\d{2})", p_id)
        year_str = year_match.group(1) if year_match else datetime.now().strftime("%y")
        year_folder = f"20{year_str}년"
        
        category_rel_path = CATEGORY_MAP.get(p_type, "")
        
        if p_type == "PS":
            parent_path = f"/{category_rel_path}/{year_folder}"
        elif p_type == "C":
            parent_path = f"/{category_rel_path}"
        else:
            parent_path = f"/{category_rel_path}/{year_folder}"
            
        project_path = f"{parent_path}/{p_id}_{p_name}"
        
        try:
            # 2. 메인 폴더 생성
            dbx.files_create_folder_v2(project_path)
            
            # 3. 하위 폴더 트리 생성
            sub_folders = FOLDER_STRUCTURES.get(p_type, [])
            for sub in sub_folders:
                dbx.files_create_folder_v2(f"{project_path}/{sub}")
                
            # 4. 공유 링크 생성 (선택 사항)
            shared_link = ""
            try:
                link_res = dbx.sharing_create_shared_link_with_settings(project_path)
                shared_link = link_res.url
            except:
                pass
                
            return True, {"path": project_path, "link": shared_link}
            
        except ApiError as e:
            if e.error.is_path() and e.error.get_path().is_already_exists():
                return False, "이미 존재하는 폴더 이름입니다."
            return False, f"드롭박스 API 오류: {e}"
        except Exception as e:
            return False, f"폴더 생성 도중 오류 발생: {str(e)}"

# 싱글톤 인스턴스
dropbox_service = DropboxService()
