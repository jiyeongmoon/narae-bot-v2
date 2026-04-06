# -*- coding: utf-8 -*-
"""
services/notion.py — 노션 API 전담 모듈
"""

import datetime
import logging
import re
import time
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
}

# 인원 DB 속성 정의
PROP_USER = {
    "name":   "이름",
    "alias":  "호칭",
    "person": "사람",
}

CLIENT_OPTIONS = ["청주시청", "괴산군청", "무주군청", "진천군청", "음성군청", "농어촌공사", "행정안전부", "나래공간", "기타"]
PHASE_OPTIONS  = ["제안·입찰", "착수", "중간보고", "최종납품"]

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
    """DB에 필요한 속성 자동 추가."""
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
        if updates:
            notion_client.databases.update(database_id=NOTION_TASK_DB_ID, properties=updates)
    except Exception as e:
        logger.error(f"DB 속성 확인 실패: {e}")


def get_notion_user_id(slack_display_name: str) -> str | None:
    """인원 DB에서 Notion 사용자 ID를 가져옴."""
    if not slack_display_name: return None
    try:
        clean_name = slack_display_name.strip()
        user_info = _get_user_info_from_db(clean_name)
        return user_info.get("person_id")
    except Exception: return None


def ensure_log_db() -> str | None:
    """일지 DB 존재 확인, 없으면 생성."""
    global NOTION_LOG_DB_ID
    if NOTION_LOG_DB_ID and len(NOTION_LOG_DB_ID) > 10:
        return NOTION_LOG_DB_ID
    try:
        task_db = notion_client.databases.retrieve(database_id=NOTION_TASK_DB_ID)
        new_db = notion_client.databases.create(
            parent=task_db.get("parent", {}),
            title=[{"type": "text", "text": {"content": "📋 일지 DB"}}],
            properties=LOG_DB_PROPERTIES,
        )
        NOTION_LOG_DB_ID = new_db["id"]
        return NOTION_LOG_DB_ID
    except Exception: return None


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
    return {
        "id": page["id"], "name": name, "deadline": deadline,
        "status": status, "assignees": assignee_names, "url": page["url"],
        "client": props.get(PROP["client"], {}).get("select", {}).get("name"),
        "phase": props.get(PROP["phase"], {}).get("select", {}).get("name"),
        "risk_flag": props.get(PROP["risk_flag"], {}).get("checkbox", False),
    }


def _get_user_info_from_db(name: str) -> dict:
    # 인원 DB 조회 실패 시 로깅을 줄이기 위해 사전 체크
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
        person_list = props[PROP_USER["person"]]["people"]
        result = {"name": db_name, "aliases": [db_name], "person_id": person_list[0]["id"] if person_list else None}
        cache_set(cache_key, result, ttl=300)
        return result
    except Exception:
        return {"name": name, "aliases": [name], "person_id": None}


def get_my_tasks(slack_display_name: str) -> list[dict]:
    try:
        user_info = _get_user_info_from_db(slack_display_name)
        my_keywords = [k.lower() for k in user_info["aliases"]]
        response = notion_client.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter=_build_active_task_filter(),
            sorts=[{"property": PROP["deadline"], "direction": "ascending"}]
        )
        my_assigned = []
        others = []
        for page in response["results"]:
            task = _parse_task(page)
            if any(any(kw in n.lower() for kw in my_keywords) for n in (task.get("assignees") or [])):
                task["is_assigned"] = True
                my_assigned.append(task)
            else:
                task["is_assigned"] = False
                others.append(task)
        return my_assigned + others
    except Exception: return []


def update_task_status(page_id: str, status_name: str) -> bool:
    try:
        notion_client.pages.update(page_id=page_id, properties={PROP["status"]: {"status": {"name": status_name}}})
        return True
    except Exception: return False


def update_task_assignee(page_id: str, slack_display_name: str) -> bool:
    try:
        user_id = get_notion_user_id(slack_display_name)
        if not user_id: return False
        notion_client.pages.update(page_id=page_id, properties={PROP["assignee"]: {"people": [{"id": user_id}]}})
        return True
    except Exception: return False


def save_log(
    task_id:       str,
    task_name:     str,
    log_date:      str,
    completed:     str,
    tomorrow:      str,
    consultation:  str = "",
    issues:        str = "",
    risk:          str = "",
    status_update: str = "",
    author_slack:  str = "",
) -> dict | None:
    """일지 DB 기록 및 Task 상세 페이지에 평면형(Flat) 블록 추가."""
    log_db_id = ensure_log_db()
    if not log_db_id: return None

    # 작성자명 정제 (Slack ID가 넘어올 경우 대비)
    display_author = author_slack
    users_info = cache_get("users_info")
    if users_info and author_slack in users_info:
        display_author = users_info[author_slack]["name"]

    try:
        # 1. 일지 DB 페이지 생성
        props = {
            "일지내용": {"title": [{"text": {"content": f"{log_date} | {task_name}"}}]},
            "날짜": {"date": {"start": log_date}},
        }
        if task_id and task_id != "NEW_TASK":
            props["연결Task"] = {"relation": [{"id": task_id}]}
        
        author_id = get_notion_user_id(author_slack)
        if author_id: props["작성자"] = {"people": [{"id": author_id}]}

        # 텍스트 속성
        for key, val in [("완료", completed), ("내일예정", tomorrow), ("협의사항", consultation), ("이슈", issues), ("리스크", risk)]:
            if val: props[key] = {"rich_text": [{"text": {"content": val[:2000]}}]}

        page = notion_client.pages.create(parent={"database_id": log_db_id}, properties=props)

        # 2. 본문 기록용 블록 리스트 (Flat 스타일 - 구분선 포함)
        flat_blocks = [
            {"object": "block", "type": "divider", "divider": {}},
            {
                "object": "block",
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": f"📅 {log_date} | ✍️ {display_author}"}}]
                }
            }
        ]

        # 섹션별 추가
        for header, text in [("✅ 완료", completed), ("🔜 내일 예정", tomorrow), ("🤝 협의", consultation), ("⚠️ 이슈", issues), ("🚨 리스크", risk)]:
            if text:
                flat_blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {"type": "text", "text": {"content": f"{header}\n"}, "annotations": {"bold": True}},
                            {"type": "text", "text": {"content": text[:2000]}}
                        ]
                    }
                })

        # 3. 페이지 본문에 추가
        notion_client.blocks.children.append(block_id=page["id"], children=flat_blocks)
        if task_id and task_id != "NEW_TASK":
            try:
                notion_client.blocks.children.append(block_id=task_id, children=flat_blocks)
                logger.info(f"Task 페이지({task_id}) 평면형 일지 추가 성공")
            except Exception as e:
                logger.error(f"Task 페이지 본문 추가 실패: {e}")

        # 4. 부대 작업
        if status_update: update_task_status(task_id, status_update)
        if author_slack: update_task_assignee(task_id, author_slack)

        return {"id": page["id"], "url": page["url"]}

    except Exception as e:
        logger.error(f"일지 기록 프로세스 실패: {e}")
        return None

append_daily_log = save_log

def get_task_todos(task_id: str) -> list[dict]:
    # 기존과 동일한 To-do 조회 로직 유지
    try:
        resp = notion_client.blocks.children.list(block_id=task_id)
        todos = []
        for block in resp.get("results", []):
            if block.get("type") == "to_do":
                raw = block["to_do"]
                text = "".join(rt["plain_text"] for rt in raw.get("rich_text", []))
                if text:
                    todos.append({"id": block["id"], "text": text, "checked": raw.get("checked", False)})
            elif block.get("type") in ("paragraph", "bulleted_list_item"):
                # 패턴 기반 To-do 파싱은 이전 turn 내용 참고
                pass
        return todos
    except Exception: return []

def update_todo_checked(block_id: str, checked: bool) -> bool:
    try:
        notion_client.blocks.update(block_id=block_id, **{"to_do": {"checked": checked}})
        return True
    except Exception: return False
