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
        """Dropbox 클라이언트 인스턴스를 반환 (토큰 자동 갱신 및 팀 공간 지원)"""
        if self.dbx is None:
            if not self.refresh_token:
                logger.error("Dropbox Refresh Token이 설정되지 않았습니다.")
                return None
            try:
                # Scoped Access 및 Refresh Token을 사용하여 클라이언트 생성
                dbx = dropbox.Dropbox(
                    app_key=self.app_key,
                    app_secret=self.app_secret,
                    oauth2_refresh_token=self.refresh_token
                )
                
                # 팀 공간(Business 계정) 대응을 위한 root_namespace_id 셋팅
                try:
                    account_info = dbx.users_get_current_account()
                    root_ns = account_info.root_info.root_namespace_id
                    self.dbx = dbx.with_path_root(dropbox.common.PathRoot.root(root_ns))
                    logger.info(f"Dropbox 연결 성공 (팀 루트 반영: {root_ns})")
                except Exception as ex:
                    # 권한 부족이나 개인 계정일 경우 폴백 처리
                    logger.warning(f"팀 루트 파악 실패, 기본 경로로 진행: {ex}")
                    self.dbx = dbx
                    logger.info("Dropbox 연결 성공 (기본 경로)")
            except Exception as e:
                logger.error(f"Dropbox 연결 실패: {e}")
                return None
        return self.dbx

    def get_next_id(self, p_type, year_str, root_override=None):
        """드롭박스 폴더를 스캔하여 다음 순번의 ID를 계산합니다."""
        dbx = self._get_client()
        if not dbx: return f"{year_str}-{p_type}01"
        
        # 스캔 베이스 경로 결정
        if root_override:
            base_path = root_override
        else:
            base_path = CATEGORY_MAP.get(p_type, "")
            
        if not base_path: return f"{year_str}-{p_type}01"
        
        # 스캔 경로 설정
        scan_paths = []
        year_folder = f"20{year_str}년"
        
        # 1. 지정된 루트 폴더 직접 탐색 (Flat 구조)
        if root_override:
            scan_paths.append(f"/{root_override}")
        else:
            # 기본 맵 기반 경로
            if p_type == "PS":
                scan_paths.append(f"/{base_path}/{year_folder}")
            elif p_type == "C":
                scan_paths.append(f"/{base_path}")
            else:
                scan_paths.append(f"/{base_path}/{year_folder}")
            
        # 2. 레거시 경로 추가 스캔
        scan_paths.append("/01_프로젝트_실무_산출물")
        
        max_sn = 0
        pattern = rf"^{re.escape(year_str)}[\-]{re.escape(p_type)}(\d+)"
        
        processed_paths = set()
        for path in scan_paths:
            if path in processed_paths or not path: continue
            processed_paths.add(path)
            try:
                res = dbx.files_list_folder(path)
                for entry in res.entries:
                    if isinstance(entry, dropbox.files.FolderMetadata):
                        match = re.search(pattern, entry.name)
                        if match:
                            sn = int(match.group(1))
                            if sn > max_sn: max_sn = sn
            except ApiError as e:
                continue
        
        next_sn = max_sn + 1
        return f"{year_str}-{p_type}{next_sn:02d}"

    def create_project_folders(self, p_id, p_name, p_type, root_override=None):
        """SOP v2.4에 맞춰 드롭박스 클라우드에 폴더를 생성합니다."""
        dbx = self._get_client()
        if not dbx: return False, "드롭박스 연결 실패"
        
        # 1. 경로 결정
        year_match = re.search(r"^(\d{2})", p_id)
        year_str = year_match.group(1) if year_match else datetime.now().strftime("%y")
        year_folder = f"20{year_str}년"
        
        if root_override:
            parent_path = f"/{root_override}"
        else:
            category_rel_path = CATEGORY_MAP.get(p_type, "")
            if p_type == "PS": parent_path = f"/{category_rel_path}/{year_folder}"
            elif p_type == "C": parent_path = f"/{category_rel_path}"
            else: parent_path = f"/{category_rel_path}/{year_folder}"
            
        project_path = f"{parent_path}/{p_id}_{p_name}"
        
        try:
            # 2. 메인 폴더 생성
            dbx.files_create_folder_v2(project_path)
            
            # 3. 하위 폴더 트리 생성
            sub_folders = FOLDER_STRUCTURES.get(p_type, [])
            for sub in sub_folders:
                dbx.files_create_folder_v2(f"{project_path}/{sub}")
                
            # 4. 공유 링크 생성
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
