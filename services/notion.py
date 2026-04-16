# -*- coding: utf-8 -*-
"""
services/notion.py — 노션 API 전담 모듈
"""

import datetime
import logging
import re
import time
import os
from notion_client import Client
from config import NOTION_TOKEN, NOTION_TASK_DB_ID, NOTION_LOG_DB_ID, NOTION_USER_DB_ID
from services.cache import get as cache_get, set as cache_set

logger = logging.getLogger(__name__)

notion_client = Client(auth=NOTION_TOKEN)

PROP = {
    "title":     "업무명",
    "status":    "진행 상황",
    "project":   "프로젝트",
    "assignee":  "담당자",
    "deadline":  "마감일자",
    "tag":       "태그",
    "result":    "주요결과",
    "client":    "발주처",
    "phase":     "현재단계",
    "risk_flag": "마감리스크",
    "participants": "참여자",
}

PROP_USER = {
    "name":   "이름",
    "alias":  "호칭",
    "person": "사람",
}

CLIENT_OPTIONS = [
    "청주시청", "괴산군청", "무주군청", "농어촌공사", "나래공간",
    "이천시청", "한국농어촌공사", "무주읍주민협의체", "진천군청", "미정", "기타",
]

# 발주처 → 대분류(지역명) 자동 매핑
CLIENT_TO_PREFIX = {
    "청주시청": "청주", "괴산군청": "괴산", "무주군청": "무주",
    "진천군청": "진천", "음성군청": "음성", "이천시청": "이천",
    "충주시청": "충주", "천안시청": "천안", "세종시청": "세종",
    "아산시청": "아산", "연기군청": "연기", "단양군청": "단양",
    "보은군청": "보은", "옥천군청": "옥천", "영동군청": "영동",
    "농어촌공사": "농공", "한국농어촌공사": "농공",
    "무주읍주민협의체": "무주읍", "행정안전부": "행안부",
    "국토교통부": "국토부", "나래공간": "내부", "미정": "미정", "기타": "기타",
}

def get_client_options_from_notion() -> list[str]:
    """Notion Task DB의 발주처(Client) select 속성에서 실제 옵션 목록을 가져옵니다.
    실패 시 하드코딩된 CLIENT_OPTIONS를 반환합니다."""
    try:
        from config import NOTION_TASK_DB_ID
        db = notion_client.databases.retrieve(database_id=NOTION_TASK_DB_ID)
        props = db.get("properties", {})
        client_prop = props.get("발주처") or props.get("Client") or {}
        prop_type = client_prop.get("type", "")
        if prop_type == "select":
            opts = [o["name"] for o in client_prop.get("select", {}).get("options", [])]
        elif prop_type == "multi_select":
            opts = [o["name"] for o in client_prop.get("multi_select", {}).get("options", [])]
        else:
            opts = []
        return opts if opts else CLIENT_OPTIONS
    except Exception as e:
        logger.warning(f"Notion 발주처 옵션 로드 실패, 기본값 사용: {e}")
        return CLIENT_OPTIONS


PHASE_OPTIONS   = ["제안·입찰", "착수", "중간보고", "최종납품"]
EXCLUDE_STATUS  = ["✅ 완료", "⏭ 보류"]
STATUS_OPTIONS  = ["🙏 진행 예정", "🚀 진행 중", "💡 피드백", "⏭ 보류", "✅ 완료"]
DEADLINE_CUTOFF_DAYS = 7

LOG_DB_PROPERTIES = {
    "일지내용": {"title": {}},
    "날짜":     {"date": {}},
    "작성자":   {"people": {}},
    "연결Task": {"relation": {
        "database_id": NOTION_TASK_DB_ID,
        "type": "single_property",
        "single_property": {},
    }},
    "카테고리": {"multi_select": {
        "options": [
            {"name": "완료",   "color": "green"},
            {"name": "예정",   "color": "blue"},
            {"name": "협의",   "color": "yellow"},
            {"name": "이슈",   "color": "orange"},
            {"name": "리스크", "color": "red"},
        ]
    }},
    "완료":     {"rich_text": {}},
    "내일예정": {"rich_text": {}},
    "협의사항": {"rich_text": {}},
    "이슈":     {"rich_text": {}},
    "리스크":   {"rich_text": {}},
}

def ensure_db_properties():
    try:
        db = notion_client.databases.retrieve(database_id=NOTION_TASK_DB_ID)
        existing = db.get("properties", {})
        updates = {}
        select_props = {PROP["client"]: CLIENT_OPTIONS, PROP["phase"]: PHASE_OPTIONS}
        for prop_name, options in select_props.items():
            if prop_name not in existing:
                updates[prop_name] = {"select": {"options": [{"name": o} for o in options]}}
        if PROP["risk_flag"] not in existing:
            updates[PROP["risk_flag"]] = {"checkbox": {}}
        if PROP["participants"] not in existing:
            updates[PROP["participants"]] = {"people": {}}
        if updates:
            notion_client.databases.update(database_id=NOTION_TASK_DB_ID, properties=updates)
    except Exception as e:
        logger.error(f"DB 속성 확인 실패: {e}")


def get_notion_user_id(slack_display_name: str) -> str | None:
    """Slack 실명 → Notion User ID.
    1순위: 노션 Users API 직접 조회 (DB 설정 불필요)
    2순위: custom User DB 조회 (fallback)
    """
    if not slack_display_name: return None
    name_lower = slack_display_name.strip().lower()
    cache_key = f"notion_uid:{name_lower}"
    cached = cache_get(cache_key)
    if cached: return cached

    # 1° Notion Users API
    try:
        users = notion_client.users.list()
        for user in users.get("results", []):
            if user.get("type") == "person":
                notion_name = user.get("name", "").lower()
                if notion_name and (name_lower in notion_name or notion_name in name_lower):
                    uid = user["id"]
                    cache_set(cache_key, uid, ttl=600)
                    logger.info(f"Notion Users API 매칭: {slack_display_name} → {uid}")
                    return uid
    except Exception as e:
        logger.warning(f"Notion Users API 조회 실패, DB fallback 시도: {e}")

    # 2° custom User DB fallback
    try:
        user_info = _get_user_info_from_db(slack_display_name.strip())
        uid = user_info.get("person_id")
        if uid: cache_set(cache_key, uid, ttl=600)
        return uid
    except Exception: return None


def ensure_log_db() -> str | None:
    global NOTION_LOG_DB_ID
    if NOTION_LOG_DB_ID and "your-log-db" not in NOTION_LOG_DB_ID and len(NOTION_LOG_DB_ID) > 10:
        return NOTION_LOG_DB_ID
    try:
        task_db = notion_client.databases.retrieve(database_id=NOTION_TASK_DB_ID)
        new_db = notion_client.databases.create(
            parent=task_db.get("parent", {}),
            title=[{"type": "text", "text": {"content": "📋 일지 DB"}}],
            properties=LOG_DB_PROPERTIES,
        )
        NOTION_LOG_DB_ID = new_db["id"]
        logger.info(f"일지 DB 신규 생성 완료: {NOTION_LOG_DB_ID}")
        return NOTION_LOG_DB_ID
    except Exception as e:
        logger.error(f"일지 DB 생성 실패: {e}")
        return None


def _build_active_task_filter() -> dict:
    return {"and": [{"property": PROP["status"], "status": {"does_not_equal": s}} for s in EXCLUDE_STATUS]}


def _parse_task(page: dict) -> dict:
    props = page["properties"]
    title_list = props.get(PROP["title"], {}).get("title", [])
    name = title_list[0]["plain_text"] if title_list else "(제목 없음)"
    
    deadline_raw = props.get(PROP["deadline"], {}).get("date")
    deadline = deadline_raw["start"] if deadline_raw else None
    
    status_raw = props.get(PROP["status"], {}).get("status")
    status = status_raw["name"] if status_raw else None
    
    assignees = props.get(PROP["assignee"], {}).get("people", [])
    assignee_names = [p.get("name", "") for p in assignees]
    assignee_ids = [p.get("id") for p in assignees if p.get("id")]
    
    client_raw = props.get(PROP["client"], {}).get("select")
    client = client_raw["name"] if client_raw else None
    
    phase_raw = props.get(PROP["phase"], {}).get("select")
    phase = phase_raw["name"] if phase_raw else None
    
    risk_flag = props.get(PROP["risk_flag"], {}).get("checkbox", False)

    return {
        "id": page["id"], "name": name, "deadline": deadline,
        "status": status, 
        "assignees": assignee_names, 
        "assignee_ids": assignee_ids,
        "url": page["url"],
        "client": client,
        "phase": phase,
        "risk_flag": risk_flag,
        "created_time": page.get("created_time"),
    }


def _get_user_info_from_db(name: str) -> dict:
    if not NOTION_USER_DB_ID or len(NOTION_USER_DB_ID) < 10:
        return {"name": name, "aliases": [name], "person_id": None}
    cache_key = f"user_info:{name}"
    cached = cache_get(cache_key)
    if cached: return cached
    try:
        response = notion_client.databases.query(
            database_id=NOTION_USER_DB_ID,
            filter={"or": [
                {"property": PROP_USER["name"], "title": {"contains": name}},
                {"property": PROP_USER["alias"], "rich_text": {"contains": name}},
            ]}
        )
        if not response["results"]: return {"name": name, "aliases": [name], "person_id": None}
        page = response["results"][0]
        props = page["properties"]
        db_name = props[PROP_USER["name"]]["title"][0]["plain_text"]
        
        # 호칭(별칭) 추출 및 분리
        alias_list = props.get(PROP_USER["alias"], {}).get("rich_text", [])
        alias_text = "".join(rt.get("plain_text", "") for rt in alias_list)
        aliases = [db_name]
        if alias_text:
            # 쉼표나 공백으로 분리된 별칭들 추가
            ext_aliases = [a.strip() for a in re.split(r'[,|/]', alias_text) if a.strip()]
            aliases.extend(ext_aliases)
        
        person_list = props[PROP_USER["person"]]["people"]
        if person_list:
            p_name = person_list[0].get("name", "")
            if p_name: aliases.append(p_name)
            
        result = {
            "name": db_name, 
            "aliases": list(set(aliases)), 
            "person_id": person_list[0]["id"] if person_list else None
        }
        cache_set(cache_key, result, ttl=300)
        return result
    except Exception: return {"name": name, "aliases": [name], "person_id": None}


def get_my_tasks(slack_display_name: str) -> list[dict]:
    try:
        user_info = _get_user_info_from_db(slack_display_name)
        my_keywords = [k.lower() for k in user_info["aliases"]]
        my_person_id = user_info.get("person_id")
        
        response = notion_client.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter=_build_active_task_filter(),
            sorts=[{"property": PROP["deadline"], "direction": "ascending"}]
        )
        tasks = [_parse_task(p) for p in response["results"]]
        for t in tasks:
            # 1. ID 매칭 (최우선)
            if my_person_id and my_person_id in t.get("assignee_ids", []):
                t["is_assigned"] = True
            else:
                # 2. 이름/별칭 매칭 (Fallback)
                t["is_assigned"] = any(any(kw in n.lower() for kw in my_keywords) for n in t.get("assignees", []))
        
        return sorted(tasks, key=lambda x: not x["is_assigned"])
    except Exception: return []

def search_tasks(keyword: str, slack_display_name: str = None) -> list[dict]:
    try:
        response = notion_client.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter={"and": [*_build_active_task_filter()["and"], {"property": PROP["title"], "title": {"contains": keyword}}]}
        )
        return [_parse_task(p) for p in response["results"]]
    except Exception: return []

def get_all_tasks() -> list[dict]:
    try:
        response = notion_client.databases.query(database_id=NOTION_TASK_DB_ID, filter=_build_active_task_filter())
        return [_parse_task(p) for p in response["results"]]
    except Exception: return []

def get_weekly_updated_tasks(only_assigned: bool = False) -> list[dict]:
    """최근 7일간 업데이트된 업무를 가져옵니다. only_assigned=True 이면 담당자가 있는 업무만 필터링합니다."""
    try:
        ago = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
        
        # 기본 필터: 7일 내 수정됨 + 활성 상태(완료/보류 제외)
        filter_conditions = [
            {"timestamp": "last_edited_time", "last_edited_time": {"after": ago}},
            *_build_active_task_filter()["and"]
        ]
        
        # 담당자가 지정된 업무만 필터링 (미배정 제외)
        if only_assigned:
            filter_conditions.append({
                "property": PROP["assignee"],
                "people": {"is_not_empty": True}
            })
            
        response = notion_client.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter={"and": filter_conditions}
        )
        return [_parse_task(p) for p in response["results"]]
    except Exception as e:
        logger.error(f"주간 업데이트 업무 조회 실패: {e}")
        return []

def create_task(task_name: str, assignee_notion_id: str = None,
                deadline: str = None, client_name: str = None,
                phase: str = None, initial_status: str = None,
                assignee_name: str = None) -> dict | None:
    properties = {
        PROP["title"]: {
            "title": [{"text": {"content": task_name}}]
        },
        PROP["status"]: {
            "status": {"name": initial_status or "🙏 진행 예정"}
        },
    }

    if assignee_notion_id:
        properties[PROP["assignee"]] = {
            "people": [{"id": assignee_notion_id}]
        }
    if deadline:
        properties[PROP["deadline"]] = {
            "date": {"start": deadline}
        }
    if client_name:
        properties[PROP["client"]] = {
            "select": {"name": client_name}
        }
    if phase:
        properties[PROP["phase"]] = {
            "select": {"name": phase}
        }

    try:
        page = notion_client.pages.create(
            parent={"database_id": NOTION_TASK_DB_ID},
            properties=properties,
        )
        page_id = page["id"]
        title_list = page["properties"][PROP["title"]]["title"]
        name = title_list[0]["plain_text"] if title_list else task_name
        logger.info(f"새 Task 생성: {name}")

        # ── 페이지 본문 초기 포맷 삽입 ──────────────────────────
        assignee_text = assignee_name or "지정 안 됨"
        client_text   = client_name or "미정"
        deadline_text = deadline    or "미정"
        phase_text    = phase       or "미정"

        body_blocks = [
            {"object": "block", "type": "heading_2",
             "heading_2": {"rich_text": [{"type": "text", "text": {"content": "[TASK 상세 내역]"}}]}},
            {"object": "block", "type": "bulleted_list_item",
             "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": f"담당자 : {assignee_text}"}}]}},
            {"object": "block", "type": "bulleted_list_item",
             "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": f"발주처 : {client_text}"}}]}},
            {"object": "block", "type": "bulleted_list_item",
             "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": f"현재단계 : {phase_text}"}}]}},
            {"object": "block", "type": "bulleted_list_item",
             "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": f"마감일 : {deadline_text}"}}]}},
            {"object": "block", "type": "paragraph",
             "paragraph": {"rich_text": []}},
            {"object": "block", "type": "paragraph",
             "paragraph": {"rich_text": [{"type": "text", "text": {"content": "\nTo-do :"}, "annotations": {"bold": True}}]}},
        ]
        try:
            notion_client.blocks.children.append(block_id=page_id, children=body_blocks)
        except Exception as be:
            logger.warning(f"Task 본문 블록 추가 실패 (기능에는 영향 없음): {be}")

        return {"id": page_id, "name": name, "url": page["url"]}

    except Exception as e:
        logger.error(f"Task 생성 실패: {e}")
        return None

def update_task_status(page_id: str, status_name: str) -> bool:
    try:
        notion_client.pages.update(page_id=page_id, properties={PROP["status"]: {"status": {"name": status_name}}})
        return True
    except Exception: return False

def update_task_assignee(page_id: str, slack_display_name: str) -> bool:
    try:
        uid = get_notion_user_id(slack_display_name)
        if uid: notion_client.pages.update(page_id=page_id, properties={PROP["assignee"]: {"people": [{"id": uid}]}})
        return True
    except Exception: return False

def update_task_assignee_by_notion_id(page_id: str, notion_user_id: str) -> bool:
    """노션 User ID를 직접 사용해 담당자 필드를 갱신합니다 (이름 매칭 없이 확실한 업데이트)."""
    if not notion_user_id: return False
    try:
        notion_client.pages.update(page_id=page_id, properties={PROP["assignee"]: {"people": [{"id": notion_user_id}]}})
        return True
    except Exception as e:
        logger.error(f"담당자 업데이트 실패(notion_id): {e}")
        return False

def update_task_risk(page_id: str, is_risk: bool) -> bool:
    """노션 Task의 '마감리스크' 체크박스 속성을 업데이트합니다."""
    try:
        notion_client.pages.update(page_id=page_id, properties={PROP["risk_flag"]: {"checkbox": is_risk}})
        logger.info(f"마감리스크 업데이트 성공: {page_id} -> {is_risk}")
        return True
    except Exception as e:
        logger.error(f"마감리스크 업데이트 실패: {e}")
        return False

def update_task_participants(page_id: str, slack_display_name: str) -> bool:
    """일지 작성자를 Task의 '참여자' 목록에 자동으로 누적 추가합니다."""
    try:
        user_id = get_notion_user_id(slack_display_name)
        if not user_id: return False

        # 기존 참여자 조회
        page = notion_client.pages.retrieve(page_id=page_id)
        current_participants = page["properties"].get(PROP["participants"], {}).get("people", [])
        
        current_ids = [p["id"] for p in current_participants]
        if user_id in current_ids:
            return True # 이미 참여자에 포함됨
            
        new_ids = current_ids + [user_id]
        notion_client.pages.update(
            page_id=page_id, 
            properties={PROP["participants"]: {"people": [{"id": uid} for uid in new_ids]}}
        )
        logger.info(f"참여자 추가 성공: {slack_display_name} -> {page_id}")
        return True
    except Exception as e:
        logger.error(f"참여자 업데이트 실패: {e}")
        return False


def _parse_daily_log_to_blocks(text: str) -> list:
    """데일리 로그 텍스트를 노션 블록 리스트로 변환합니다.

    입력 규칙:
      빈 줄           : 단락 구분 (current_top_bullet 초기화)
      - text          : 서브 불릿 (직전 메인 불릿의 자식)
      • text 또는 기타 : 메인 불릿 (bulleted_list_item)
    
    예시 입력:
      wwwww            → • wwwww (메인 불릿)
      - ddffdf         →   - ddffdf (서브 불릿)
      - dfdfdf         →   - dfdfdf (서브 불릿)
      dfddth...        → • dfddth... (메인 불릿)
    """
    blocks = []
    current_top_bullet = None

    for raw_line in text.splitlines():
        if not raw_line.strip():
            current_top_bullet = None
            continue

        stripped = raw_line.strip()

        # - 로 시작하면 서브 불릿
        if stripped.startswith("- "):
            content = stripped[2:].strip()
            sub = {
                "object": "block", "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": content[:2000]}}]
                }
            }
            if current_top_bullet is not None:
                current_top_bullet["bulleted_list_item"].setdefault("children", []).append(sub)
            else:
                blocks.append(sub)

        else:
            # 일반 텍스트 또는 • 접두사 → 메인 불릿
            if stripped.startswith("• "):
                content = stripped[2:].strip()
            else:
                content = stripped
            block = {
                "object": "block", "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": content[:2000]}}]
                }
            }
            blocks.append(block)
            current_top_bullet = block

    return blocks




def save_log(task_id, task_name, log_date, completed, todo_add,
             consultation="", issues="", risk="", status_update="", author_slack="",
             is_new_task=False, daily_log=""):
    log_db_id = ensure_log_db()
    if not log_db_id:
        logger.warning("일지 DB를 쓸 수 없으므로 중앙 DB 기록은 생략하고 Task 페이지에만 기록합니다.")

    display_author = author_slack
    uinfo = cache_get("users_info")
    if uinfo and author_slack in uinfo: 
        display_author = uinfo[author_slack]["name"]
    author_name = display_author # 실명 표기용

    try:
        toggle_children = []
        for h, t in [("✅ 오늘 완료", completed), ("📝 데일리 로그", daily_log), ("📌 To-do 추가", todo_add), ("🤝 협의", consultation), ("⚠️ 이슈", issues), ("🚨 리스크", risk)]:
            if not t:
                continue

            # 헤더 단락 추가 (공통)
            toggle_children.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": h}, "annotations": {"bold": True}}]}
            })

            if h == "📝 데일리 로그":
                # 데일리 로그: 마크다운 스타일 파싱 (불릿, 서브 불릿, 단락)
                toggle_children.extend(_parse_daily_log_to_blocks(t))

            elif h in ("✅ 오늘 완료", "📌 To-do 추가"):
                # 완료·To-do: 줄별 불릿 리스트
                for line in t.splitlines():
                    line = line.strip()
                    if line:
                        if line.startswith("• "): line = line[2:]
                        elif line.startswith("- "): line = line[2:]
                        toggle_children.append({
                            "object": "block", "type": "bulleted_list_item",
                            "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": line[:2000]}}]}
                        })
            else:
                # 협의, 이슈, 리스크: 인라인 단락 (헤더 단락 제거 후 합산)
                toggle_children.pop()  # 위에서 추가한 헤더 단락 제거
                toggle_children.append({
                    "object": "block", "type": "paragraph",
                    "paragraph": {"rich_text": [
                        {"type": "text", "text": {"content": f"{h}  "}, "annotations": {"bold": True}},
                        {"type": "text", "text": {"content": t[:2000]}}
                    ]}
                })


        # 로그 엔트리 제목 블록 (토글 적용)
        blocks = [
            {"object": "block", "type": "divider", "divider": {}},
            {
                "object": "block", 
                "type": "heading_3", 
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": f"📅 {log_date} | ✍️ {display_author}"}}],
                    "is_toggleable": True
                }
            }
        ]
        
        # To-do 블록: todo_add 입력 항목만 미완료 체크박스로 생성
        todo_blocks = []
        if todo_add:
            for line in [l.strip() for l in todo_add.splitlines() if l.strip()]:
                if line.startswith("• "): line = line[2:]
                elif line.startswith("- "): line = line[2:]
                todo_text = f"{line} ({author_name})" if author_name else line
                todo_blocks.append({
                    "object": "block", "type": "to_do",
                    "to_do": {"rich_text": [{"type": "text", "text": {"content": todo_text}}], "checked": False}
                })

        def _append_log_blocks(target_id):
            res = notion_client.blocks.children.append(block_id=target_id, children=blocks)
            if toggle_children and "results" in res and res["results"]:
                # 방금 추가한 heading_3 블록의 ID를 가져와 자식 요소를 추가합니다.
                toggle_id = res["results"][-1]["id"]
                notion_client.blocks.children.append(block_id=toggle_id, children=toggle_children)

        page_id = None
        page_url = None

        if log_db_id:
            props = {
                "일지내용": {"title": [{"text": {"content": f"{log_date} | {task_name}"}}]},
                "날짜": {"date": {"start": log_date}},
            }
            if task_id and not task_id.startswith("NEW_TASK"): props["연결Task"] = {"relation": [{"id": task_id}]}
            aid = get_notion_user_id(author_slack)
            if aid: props["작성자"] = {"people": [{"id": aid}]}
            for k, v in [("완료", completed), ("데일리로그", daily_log), ("To-do추가", todo_add), ("협의사항", consultation), ("이슈", issues), ("리스크", risk)]:
                if v: props[k] = {"rich_text": [{"text": {"content": v[:2000]}}]}

            page = notion_client.pages.create(parent={"database_id": log_db_id}, properties=props)
            page_id = page["id"]
            page_url = page["url"]
            _append_log_blocks(page_id)

        if task_id and not task_id.startswith("NEW_TASK"):
            try:
                if is_new_task:
                    # 새 Task: To-do 먼저 (To-do 섹션에 위치) → 로그 나중에
                    if todo_blocks:
                        notion_client.blocks.children.append(block_id=task_id, children=todo_blocks)
                    _append_log_blocks(task_id)
                else:
                    # 기존 Task: 로그 먼저 (하단 추가) → 내일예정만 to_do 섹션에 삽입
                    _append_log_blocks(task_id)
                    if todo_blocks:
                        try:
                            resp = notion_client.blocks.children.list(block_id=task_id)
                            last_todo_id = None
                            for b in resp.get("results", []):
                                if b.get("type") == "to_do":
                                    last_todo_id = b["id"]
                            if last_todo_id:
                                notion_client.blocks.children.append(
                                    block_id=task_id, children=todo_blocks, after=last_todo_id
                                )
                            else:
                                notion_client.blocks.children.append(block_id=task_id, children=todo_blocks)
                        except Exception as te:
                            logger.warning(f"To-do 섹션 삽입 실패, 하단에 추가: {te}")
                            notion_client.blocks.children.append(block_id=task_id, children=todo_blocks)
            except Exception as e:
                logger.error(f"Task 상세 페이지 기록 실패: {e}")

        if status_update: update_task_status(task_id, status_update)
        # 담당자 보호 강화: 담당자는 편집하지 않고, 일지 작성자를 '참여자'로 자동 등록
        if author_slack: update_task_participants(task_id, author_slack)
        if risk: update_task_risk(task_id, True)

        return {"id": page_id or task_id, "url": page_url or f"https://notion.so/{task_id.replace('-', '')}"}
    except Exception as e:
        logger.error(f"일지 기록 실패: {e}")
        return None

append_daily_log = save_log


def get_task_todos(task_id: str) -> list[dict]:
    TODO_PATTERN = re.compile(r"^\s*-\s*\[(x|o| )\]\s*(.+)$", re.IGNORECASE)
    def _fetch(bid, depth=0):
        if depth > 3: return []
        todos = []
        try:
            resp = notion_client.blocks.children.list(block_id=bid)
            for b in resp.get("results", []):
                bt = b.get("type", "")
                if bt == "to_do":
                    t = "".join(rt["plain_text"] for rt in b["to_do"].get("rich_text", []))
                    if t: todos.append({"id": b["id"], "text": t, "checked": b["to_do"].get("checked", False), "block_type": "to_do"})
                elif bt in ("paragraph", "bulleted_list_item"):
                    txt = "".join(rt["plain_text"] for rt in b[bt].get("rich_text", []))
                    for i, ln in enumerate(txt.splitlines()):
                        m = TODO_PATTERN.match(ln)
                        if m: todos.append({"id": f"{b['id']}::line_{i}", "text": m.group(2).strip(), "checked": m.group(1).lower() in ("x","o"), "block_type": "text_pattern"})
                if b.get("has_children"): todos.extend(_fetch(b["id"], depth+1))
        except Exception: pass
        return todos
    return _fetch(task_id)


def update_todo_checked(block_id: str, checked: bool, author_name: str = "") -> bool:
    """체크박스 상태를 업데이트하며, 체크 시 작성자 이름을 텍스트 뒤에 추가/삭제합니다."""
    try:
        tag = f" ({author_name})" if author_name else ""
        
        if "::line_" in block_id:
            bid, lstr = block_id.split("::line_")
            lidx = int(lstr)
            b = notion_client.blocks.retrieve(block_id=bid)
            bt = b.get("type", "")
            rts = b.get(bt, {}).get("rich_text", [])
            for rt in rts:
                content = rt.get("text", {}).get("content", "")
                lns = content.splitlines(keepends=True)
                for i, ln in enumerate(lns):
                    if i == lidx:
                        if checked:
                            # 체크 시: - [ ] -> - [o] 및 이름 추가
                            ln = ln.replace("- [ ]", "- [o]").replace("- [x]", "- [o]")
                            if tag and tag not in ln:
                                ln = ln.rstrip() + tag + "\n"
                        else:
                            # 체크 해제 시: - [o] -> - [ ] 및 이름 삭제
                            ln = ln.replace("- [o]", "- [ ]").replace("- [x]", "- [ ]")
                            if tag:
                                ln = ln.replace(tag, "")
                        lns[i] = ln
                rt["text"]["content"] = "".join(lns)
                rt.pop("plain_text", None)
            notion_client.blocks.update(block_id=bid, **{bt: {"rich_text": rts}})
            return True

        # 실제 to_do 블록 처리
        if checked:
            # 체크 시 텍스트 업데이트 후 체크 상태 변경
            try:
                b = notion_client.blocks.retrieve(block_id=block_id)
                rts = b.get("to_do", {}).get("rich_text", [])
                if rts:
                    content = rts[0].get("text", {}).get("content", "")
                    if tag and tag not in content:
                        rts[0]["text"]["content"] = content + tag
                        notion_client.blocks.update(block_id=block_id, to_do={"rich_text": rts})
            except: pass
            notion_client.blocks.update(block_id=block_id, to_do={"checked": True})
        else:
            # 체크 해제 시 이름 삭제 시도 후 체크 해제
            try:
                b = notion_client.blocks.retrieve(block_id=block_id)
                rts = b.get("to_do", {}).get("rich_text", [])
                if rts and tag:
                    content = rts[0].get("text", {}).get("content", "")
                    if tag in content:
                        rts[0]["text"]["content"] = content.replace(tag, "")
                        notion_client.blocks.update(block_id=block_id, to_do={"rich_text": rts})
            except: pass
            notion_client.blocks.update(block_id=block_id, to_do={"checked": False})
        return True
    except Exception as e:
        logger.error(f"To-do 체크 업데이트 실패: {e}")
        return False


def delete_todo_block(block_id: str) -> bool:
    """완료된 To-do 블록을 노션 섹션에서 삭제합니다.
    
    text_pattern(- [ ] 텍스트) 타입은 실제 블록 삭제가 아닌 라인 제거로 처리합니다.
    실제 to_do 블록 타입은 blocks.delete API로 제거합니다.
    """
    try:
        if "::line_" in block_id:
            # text_pattern: 해당 라인만 텍스트에서 제거
            bid, lstr = block_id.split("::line_")
            lidx = int(lstr)
            b = notion_client.blocks.retrieve(block_id=bid)
            bt = b.get("type", "")
            rts = b.get(bt, {}).get("rich_text", [])
            for rt in rts:
                content = rt.get("text", {}).get("content", "")
                lines = content.splitlines(keepends=True)
                if 0 <= lidx < len(lines):
                    lines.pop(lidx)
                new_content = "".join(lines).strip()
                if new_content:
                    rt["text"]["content"] = new_content
                    rt.pop("plain_text", None)
                    notion_client.blocks.update(block_id=bid, **{bt: {"rich_text": rts}})
                else:
                    # 라인이 없으면 블록 자체 삭제
                    notion_client.blocks.delete(block_id=bid)
        else:
            # 실제 to_do 블록 삭제
            notion_client.blocks.delete(block_id=block_id)
        return True
    except Exception as e:
        logger.error(f"To-do 블록 삭제 실패 ({block_id}): {e}")
        return False


def replace_text_pattern_todos(task_id: str, all_todos: list, checked_ids: set, author_name: str = "") -> bool:
    """- [ ] 텍스트 형식 to-do를 실제 Notion to_do 블록으로 교체합니다. (Make & 나래봇 공용)
    
    1. 원본 블록 내 할 일 텍스트 외의 내용(예: 'To-do :' 헤더)이 있으면 해당 텍스트만 업데이트하여 보존합니다.
    2. 할 일 목록 전체가 한 블록이면 블록을 삭제합니다.
    3. 미체크된 항목은 실제 Notion to_do 블록으로 변환하여 적절한 위치(To-do 섹션 내)에 삽입합니다.
    """
    text_pattern_todos = [t for t in all_todos if t.get("block_type") == "text_pattern"]
    if not text_pattern_todos:
        return True

    try:
        # 1. 원본 텍스트 블록 처리 (전체 삭제 또는 부분 업데이트)
        block_groups = {}
        for t in text_pattern_todos:
            bid = t["id"].split("::")[0]
            if bid not in block_groups: block_groups[bid] = []
            block_groups[bid].append(t)

        for bid, items in block_groups.items():
            try:
                b = notion_client.blocks.retrieve(block_id=bid)
                bt = b.get("type", "")
                rts = b.get(bt, {}).get("rich_text", [])
                
                full_txt = "".join(rt.get("plain_text", "") for rt in rts)
                lines = full_txt.splitlines(keepends=True)
                
                # 할 일 라인이 아닌 '남겨야 할 텍스트(예: 헤더)'만 필터링
                remaining_lines = []
                for i, ln in enumerate(lines):
                    is_todo_line = any(f"{bid}::line_{i}" == it["id"] for it in items)
                    # 할 일 패턴이 아니고 빈 줄이 아니면 남겨둠
                    if not is_todo_line and ln.strip(): 
                        remaining_lines.append(ln)
                
                if not remaining_lines:
                    notion_client.blocks.delete(block_id=bid)
                else:
                    new_txt = "".join(remaining_lines).strip()
                    new_rts = [{"type": "text", "text": {"content": new_txt}}]
                    notion_client.blocks.update(block_id=bid, **{bt: {"rich_text": new_rts}})
            except Exception as e:
                logger.warning(f"원본 블록({bid}) 처리 실패: {e}")

        # 3. 최적의 삽입 위치 탐색 (To-do 섹션 찾기)
        if text_pattern_todos:
            # 완료 항목 → checked=True, 미완료 항목 → checked=False 로 변환
            tag = f" ({author_name})" if author_name else ""
            new_todo_blocks = []
            for t in text_pattern_todos:
                is_checked = t["id"] in checked_ids
                text = t["text"]
                if is_checked and tag and tag not in text:
                    text += tag

                new_todo_blocks.append({
                    "object": "block", "type": "to_do",
                    "to_do": {
                        "rich_text": [{"type": "text", "text": {"content": text}}],
                        "checked": is_checked
                    }
                })


            # 페이지 전체를 훑으며 'To-do' 앵커 찾기 (재귀 탐색)
            insert_point = {"parent_id": task_id, "after_id": None}
            
            def _scan(bid):
                try:
                    res = notion_client.blocks.children.list(block_id=bid)
                    l_todo = None
                    t_head = None
                    for b in res.get("results", []):
                        if b["id"] == bid: continue # 무한 루프 방지
                        bt = b.get("type", "")
                        
                        # 1순위: 가장 마지막에 있는 실제 to_do 블록
                        if bt == "to_do": 
                            l_todo = b["id"]
                        # 2순위: "To-do" 텍스트가 포함된 모든 블록 형식 대응
                        elif bt in ("paragraph", "bulleted_list_item", "heading_1", "heading_2", "heading_3", "callout"):
                            props = b.get(bt, {})
                            txt = "".join(rt.get("plain_text", "") for rt in props.get("rich_text", []))
                            if "To-do" in txt: 
                                t_head = b["id"]
                                # 헤더 바로 아래에 자식으로 넣어야 할지 결정하기 위해 더 깊이 탐색
                                if b.get("has_children"):
                                    _scan(b["id"])
                                    if insert_point["after_id"]: return
                        
                        # 하위 자식이 있으면 탐색 (Make Task 구조 대응)
                        if b.get("has_children") and not t_head:
                            _scan(b["id"])
                            if insert_point["after_id"]: return
                    
                    if l_todo or t_head:
                        insert_point["parent_id"] = bid
                        insert_point["after_id"] = l_todo or t_head
                except: pass

            _scan(task_id)
            
            # 최종 삽입 (after_id가 없으면 맨 아래에 추가)
            if insert_point["after_id"]:
                notion_client.blocks.children.append(
                    block_id=insert_point["parent_id"], 
                    children=new_todo_blocks, 
                    after=insert_point["after_id"]
                )
            else:
                notion_client.blocks.children.append(block_id=task_id, children=new_todo_blocks)

        return True
    except Exception as e:
        logger.error(f"To-do 변환 실패: {e}")
        return False


def get_handover_data(task_id: str) -> list[dict]:
    if not NOTION_LOG_DB_ID or len(NOTION_LOG_DB_ID) < 10: return []
    try:
        resp = notion_client.databases.query(database_id=NOTION_LOG_DB_ID, filter={"property": "연결Task", "relation": {"contains": task_id}})
        res = []
        for p in resp["results"]:
            props = p["properties"]
            def _rt(k):
                r = props.get(k, {}).get("rich_text", [])
                return r[0]["plain_text"] if r else ""
            res.append({"date": props.get("날짜", {}).get("date", {}).get("start", ""), "author": (props.get("작성자", {}).get("people", []) or [{"name": "미상"}])[0]["name"], "issues": _rt("이슈"), "risk": _rt("리스크")})
        return res
    except Exception: return []


def get_latest_risk_from_blocks(page_id: str) -> str:
    """페이지 본문 블록에서 '🚨 리스크' 패턴을 찾아 내용을 추출합니다."""
    try:
        # 1. 페이지의 최상위 블록들을 가져옵니다.
        blocks = notion_client.blocks.children.list(block_id=page_id).get("results", [])
        
        # 2. 역순으로 훑으며 '📅'가 포함된 토글(heading_3) 또는 '🚨 리스크'가 포함된 단락을 찾습니다.
        for block in reversed(blocks):
            b_type = block.get("type")
            
            # 토글(heading_3) 내부에 리스크 내용이 있는지 재귀적으로 확인
            if b_type == "heading_3" and block["heading_3"].get("is_toggleable"):
                child_blocks = notion_client.blocks.children.list(block_id=block["id"]).get("results", [])
                for cb in child_blocks:
                    if cb.get("type") == "paragraph":
                        rich_text = cb["paragraph"].get("rich_text", [])
                        text_content = "".join([rt.get("plain_text", "") for rt in rich_text])
                        if "🚨 리스크" in text_content:
                            return text_content.replace("🚨 리스크", "").strip()

            # 단락 자체가 리스크 내용인 경우
            if b_type == "paragraph":
                rich_text = block["paragraph"].get("rich_text", [])
                text_content = "".join([rt.get("plain_text", "") for rt in rich_text])
                if "🚨 리스크" in text_content:
                    return text_content.replace("🚨 리스크", "").strip()
        
        return ""
    except Exception as e:
        logger.warning(f"블록 내 리스크 추출 실패({page_id}): {e}")
        return ""

def get_deadline_risk_tasks() -> list[dict]:
    """마감리스크가 체크된 모든 업무와 최근의 리스크 상세 내용을 가져옵니다."""
    try:
        response = notion_client.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter={"property": PROP["risk_flag"], "checkbox": {"equals": True}}
        )
        tasks = [_parse_task(p) for p in response["results"]]
        logger.info(f"마감리스크 태스크 {len(tasks)}건 감지됨.")
        
        for t in tasks:
            t["risk_content"] = ""
            
            # 우선적으로 페이지 본문(Blocks)에서 리스크 내용을 직접 추출 시도 (사용자 요구사항)
            content = get_latest_risk_from_blocks(t["id"])
            
            # 본문에 없으면 기존처럼 일지 DB(Log DB) 속성에서 조회 시도
            if not content and NOTION_LOG_DB_ID and len(NOTION_LOG_DB_ID) > 10:
                try:
                    log_resp = notion_client.databases.query(
                        database_id=NOTION_LOG_DB_ID,
                        filter={
                            "and": [
                                {"property": "연결Task", "relation": {"contains": t["id"]}},
                                {"property": "리스크", "rich_text": {"is_not_empty": True}}
                            ]
                        },
                        sorts=[{"property": "날짜", "direction": "descending"}],
                        page_size=1
                    )
                    if log_resp["results"]:
                        risk_props = log_resp["results"][0]["properties"]
                        r_text = risk_props.get("리스크", {}).get("rich_text", [])
                        content = "".join([rt.get("plain_text", "") for rt in r_text]).strip()
                except Exception:
                    pass

            t["risk_content"] = content
        return tasks
    except Exception as e:
        logger.error(f"마감리스크 태스크 조회 실패: {e}")
        return []


def get_weekly_logs() -> list[dict]:

    """최근 7일간 일지 DB(Log DB)에 작성된 모든 일지를 수집합니다."""
    log_db_id = ensure_log_db()
    if not log_db_id: return []
    try:
        ago = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
        response = notion_client.databases.query(
            database_id=log_db_id,
            filter={"property": "날짜", "date": {"on_or_after": ago}},
            sorts=[{"property": "날짜", "direction": "descending"}]
        )
        logs = []
        for page in response["results"]:
            props = page["properties"]
            
            # 작성자 (People 속성)
            people = props.get("작성자", {}).get("people", [])
            author_name = people[0].get("name", "알 수 없음") if people else "알 수 없음"
            
            # 연결된 Task 정보 (Relation)
            task_ids = [r["id"] for r in props.get("연결Task", {}).get("relation", [])]
            task_name = "(연결된 업무 없음)"
            task_url = ""
            status = ""
            client = ""
            
            if task_ids:
                try:
                    task_page = notion_client.pages.retrieve(page_id=task_ids[0])
                    t_props = task_page["properties"]
                    t_name_list = t_props.get(PROP["title"], {}).get("title", [])
                    task_name = t_name_list[0]["plain_text"] if t_name_list else "(제목 없음)"
                    task_url = task_page["url"]
                    status = t_props.get(PROP["status"], {}).get("status", {}).get("name", "")
                    client = t_props.get(PROP["client"], {}).get("select", {}).get("name", "")
                except: pass

            def _rt(k):
                r = props.get(k, {}).get("rich_text", [])
                return "".join([rt.get("plain_text", "") for rt in r])

            logs.append({
                "author": author_name,
                "date": props.get("날짜", {}).get("date", {}).get("start", ""),
                "task_name": task_name,
                "task_url": task_url,
                "status": status,
                "client": client,
                "completed": _rt("완료"),
                "tomorrow": _rt("내일예정"),
                "consultation": _rt("협의사항"),
                "issues": _rt("이슈"),
                "risk": _rt("리스크"),
            })
        return logs
    except Exception as e:
        logger.error(f"주간 일지 조회 실패: {e}")
        return []
