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
    
    client_raw = props.get(PROP["client"], {}).get("select")
    client = client_raw["name"] if client_raw else None
    
    phase_raw = props.get(PROP["phase"], {}).get("select")
    phase = phase_raw["name"] if phase_raw else None
    
    risk_flag = props.get(PROP["risk_flag"], {}).get("checkbox", False)

    return {
        "id": page["id"], "name": name, "deadline": deadline,
        "status": status, "assignees": assignee_names, "url": page["url"],
        "client": client,
        "phase": phase,
        "risk_flag": risk_flag,
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
        person_list = props[PROP_USER["person"]]["people"]
        result = {"name": db_name, "aliases": [db_name], "person_id": person_list[0]["id"] if person_list else None}
        cache_set(cache_key, result, ttl=300)
        return result
    except Exception: return {"name": name, "aliases": [name], "person_id": None}


def get_my_tasks(slack_display_name: str) -> list[dict]:
    try:
        user_info = _get_user_info_from_db(slack_display_name)
        my_keywords = [k.lower() for k in user_info["aliases"]]
        response = notion_client.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter=_build_active_task_filter(),
            sorts=[{"property": PROP["deadline"], "direction": "ascending"}]
        )
        tasks = [_parse_task(p) for p in response["results"]]
        for t in tasks:
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

def get_weekly_updated_tasks() -> list[dict]:
    try:
        ago = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
        response = notion_client.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter={"and": [{"timestamp": "last_edited_time", "last_edited_time": {"after": ago}}, *_build_active_task_filter()["and"]]}
        )
        return [_parse_task(p) for p in response["results"]]
    except Exception: return []

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
             "paragraph": {"rich_text": [{"type": "text", "text": {"content": "To-do :"}, "annotations": {"bold": True}}]}},
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


def save_log(task_id, task_name, log_date, completed, tomorrow,
             consultation="", issues="", risk="", status_update="", author_slack="",
             is_new_task=False):
    log_db_id = ensure_log_db()
    if not log_db_id:
        logger.warning("일지 DB를 쓸 수 없으므로 중앙 DB 기록은 생략하고 Task 페이지에만 기록합니다.")

    display_author = author_slack
    uinfo = cache_get("users_info")
    if uinfo and author_slack in uinfo: display_author = uinfo[author_slack]["name"]

    try:
        # 로그 엔트리 텍스트 블록
        blocks = [
            {"object": "block", "type": "divider", "divider": {}},
            {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [{"type": "text", "text": {"content": f"📅 {log_date} | ✍️ {display_author}"}}]}}
        ]
        for h, t in [("✅ 완료", completed), ("🔜 내일 예정", tomorrow), ("🤝 협의", consultation), ("⚠️ 이슈", issues), ("🚨 리스크", risk)]:
            if t: blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [
                    {"type": "text", "text": {"content": f"{h}  "}, "annotations": {"bold": True}},
                    {"type": "text", "text": {"content": t[:2000]}}
                ]}
            })

        # ── To-do 블록 생성 ──────────────────────────────────────────────
        # • 새 Task: 완료(체크) + 내일예정(미체크) 모두 To-do 섹션에 삽입
        # • 기존 Task: 내일예정(미체크)만 삽입 (완료항목은 모달 체크 선택으로 이미 처리, 중복 생성 방지)
        todo_blocks = []
        if is_new_task and completed:
            for line in [l.strip() for l in completed.splitlines() if l.strip()]:
                todo_blocks.append({
                    "object": "block", "type": "to_do",
                    "to_do": {"rich_text": [{"type": "text", "text": {"content": line}}], "checked": True}
                })
        if tomorrow:
            for line in [l.strip() for l in tomorrow.splitlines() if l.strip()]:
                todo_blocks.append({
                    "object": "block", "type": "to_do",
                    "to_do": {"rich_text": [{"type": "text", "text": {"content": line}}], "checked": False}
                })

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
            for k, v in [("완료", completed), ("내일예정", tomorrow), ("협의사항", consultation), ("이슈", issues), ("리스크", risk)]:
                if v: props[k] = {"rich_text": [{"text": {"content": v[:2000]}}]}

            page = notion_client.pages.create(parent={"database_id": log_db_id}, properties=props)
            page_id = page["id"]
            page_url = page["url"]
            notion_client.blocks.children.append(block_id=page_id, children=blocks)

        if task_id and not task_id.startswith("NEW_TASK"):
            try:
                if is_new_task:
                    # 새 Task: To-do 먼저 (To-do 섹션에 위치) → 로그 나중에
                    if todo_blocks:
                        notion_client.blocks.children.append(block_id=task_id, children=todo_blocks)
                    notion_client.blocks.children.append(block_id=task_id, children=blocks)
                else:
                    # 기존 Task: 로그 먼저 (하단 추가) → 내일예정만 to_do 섹션에 삽입
                    notion_client.blocks.children.append(block_id=task_id, children=blocks)
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
        if author_slack: update_task_assignee(task_id, author_slack)

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


def update_todo_checked(block_id: str, checked: bool) -> bool:
    try:
        if "::line_" in block_id:
            bid, lstr = block_id.split("::line_")
            lidx = int(lstr)
            b = notion_client.blocks.retrieve(block_id=bid)
            bt = b.get("type", "")
            rts = b.get(bt, {}).get("rich_text", [])
            for rt in rts:
                lns = rt.get("text", {}).get("content", "").splitlines(keepends=True)
                for i, ln in enumerate(lns):
                    if i == lidx:
                        if checked: ln = ln.replace("- [ ]", "- [o]").replace("- [x]", "- [o]")
                        else: ln = ln.replace("- [o]", "- [ ]").replace("- [x]", "- [ ]")
                        lns[i] = ln
                rt["text"]["content"] = "".join(lns)
                rt.pop("plain_text", None)
            notion_client.blocks.update(block_id=bid, **{bt: {"rich_text": rts}})
            return True
        notion_client.blocks.update(block_id=block_id, **{"to_do": {"checked": checked}})
        return True
    except Exception: return False


def replace_text_pattern_todos(task_id: str, all_todos: list, checked_ids: set) -> bool:
    """- [ ] 텍스트 형식 to-do를 실제 Notion to_do 블록으로 교체합니다.
    
    - 체크된 항목 : 텍스트 블록에서 제거 (이미 '오늘 완료' 로그에 반영됨)
    - 미체크 항목 : 텍스트 블록 제거 후 실제 to_do 블록으로 삽입
    """
    text_pattern_todos = [t for t in all_todos if t.get("block_type") == "text_pattern"]
    if not text_pattern_todos:
        return True

    try:
        # 1. 미체크 항목만 to_do 블록으로 변환할 텍스트 수집
        unchecked_texts = [
            t["text"] for t in text_pattern_todos
            if t["id"] not in checked_ids
        ]

        # 2. 원본 텍스트 블록 삭제 (중복 parent block은 한 번만)
        parent_block_ids = list({t["id"].split("::")[0] for t in text_pattern_todos})
        for bid in parent_block_ids:
            try:
                notion_client.blocks.delete(block_id=bid)
            except Exception as de:
                logger.warning(f"텍스트 블록 삭제 실패 ({bid}): {de}")

        # 3. 미체크 항목을 실제 to_do 블록으로 삽입
        if unchecked_texts:
            new_todo_blocks = [
                {
                    "object": "block", "type": "to_do",
                    "to_do": {"rich_text": [{"type": "text", "text": {"content": t}}], "checked": False}
                }
                for t in unchecked_texts
            ]
            # Task 페이지의 마지막 to_do 블록 이후 삽입 (To-do 섹션 유지)
            resp = notion_client.blocks.children.list(block_id=task_id)
            last_todo_id = None
            # "To-do :" 헤더 paragraph도 찾아서 fallback으로 활용
            todo_header_id = None
            for b in resp.get("results", []):
                bt = b.get("type", "")
                if bt == "to_do":
                    last_todo_id = b["id"]
                elif bt == "paragraph":
                    txt = "".join(rt.get("plain_text", "") for rt in b["paragraph"].get("rich_text", []))
                    if "To-do" in txt:
                        todo_header_id = b["id"]

            after_id = last_todo_id or todo_header_id
            if after_id:
                notion_client.blocks.children.append(
                    block_id=task_id, children=new_todo_blocks, after=after_id
                )
            else:
                notion_client.blocks.children.append(block_id=task_id, children=new_todo_blocks)

        return True
    except Exception as e:
        logger.error(f"text_pattern to_do 변환 실패: {e}")
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
