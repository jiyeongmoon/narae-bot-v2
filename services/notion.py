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

CLIENT_OPTIONS = ["청주시청", "괴산군청", "무주군청", "진천군청", "음성군청", "농어촌공사", "행정안전부", "나래공간", "기타"]
PHASE_OPTIONS  = ["제안·입찰", "착수", "중간보고", "최종납품"]

EXCLUDE_STATUS  = ["✅ 완료", "⏭ 보류"]
STATUS_OPTIONS  = ["🙏 진행 예정", "🚀 진행 중", "💡 피드백", "⏭ 보류", "✅ 완료"]
DEADLINE_CUTOFF_DAYS = 7


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
    if not slack_display_name: return None
    try:
        user_info = _get_user_info_from_db(slack_display_name.strip())
        return user_info.get("person_id")
    except Exception: return None


def ensure_log_db() -> str | None:
    global NOTION_LOG_DB_ID
    if NOTION_LOG_DB_ID and len(NOTION_LOG_DB_ID) > 10: return NOTION_LOG_DB_ID
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
    return {
        "id": page["id"], "name": name, "deadline": deadline,
        "status": status, "assignees": assignee_names, "url": page["url"],
        "client": props.get(PROP["client"], {}).get("select", {}).get("name"),
        "phase": props.get(PROP["phase"], {}).get("select", {}).get("name"),
        "risk_flag": props.get(PROP["risk_flag"], {}).get("checkbox", False),
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


def save_log(task_id, task_name, log_date, completed, tomorrow, consultation="", issues="", risk="", status_update="", author_slack=""):
    log_db_id = ensure_log_db()
    if not log_db_id: return None
    display_author = author_slack
    uinfo = cache_get("users_info")
    if uinfo and author_slack in uinfo: display_author = uinfo[author_slack]["name"]

    try:
        props = {
            "일지내용": {"title": [{"text": {"content": f"{log_date} | {task_name}"}}]},
            "날짜": {"date": {"start": log_date}},
        }
        if task_id and task_id != "NEW_TASK": props["연결Task"] = {"relation": [{"id": task_id}]}
        aid = get_notion_user_id(author_slack)
        if aid: props["작성자"] = {"people": [{"id": aid}]}
        for k, v in [("완료", completed), ("내일예정", tomorrow), ("협의사항", consultation), ("이슈", issues), ("리스크", risk)]:
            if v: props[k] = {"rich_text": [{"text": {"content": v[:2000]}}]}
        
        page = notion_client.pages.create(parent={"database_id": log_db_id}, properties=props)

        # Flat Style Blocks
        blocks = [
            {"object": "block", "type": "divider", "divider": {}},
            {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [{"type": "text", "text": {"content": f"📅 {log_date} | ✍️ {display_author}"}}]}}
        ]
        for h, t in [("✅ 완료", completed), ("🔜 내일 예정", tomorrow), ("🤝 협의", consultation), ("⚠️ 이슈", issues), ("🚨 리스크", risk)]:
            if t: blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"{h}\n", "annotations": {"bold": True}}}, {"type": "text", "text": {"content": t[:2000]}}]}})

        notion_client.blocks.children.append(block_id=page["id"], children=blocks)
        if task_id and task_id != "NEW_TASK":
            try: notion_client.blocks.children.append(block_id=task_id, children=blocks)
            except Exception: pass
        
        if status_update: update_task_status(task_id, status_update)
        if author_slack: update_task_assignee(task_id, author_slack)
        return {"id": page["id"], "url": page["url"]}
    except Exception: return None

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
