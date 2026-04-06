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
    """DB에 발주처/현재단계/마감리스크 속성이 없으면 자동 추가. 멱등."""
    try:
        db = notion_client.databases.retrieve(database_id=NOTION_TASK_DB_ID)
        existing = db.get("properties", {})

        updates = {}
        select_props = {
            PROP["client"]: CLIENT_OPTIONS,
            PROP["phase"]:  PHASE_OPTIONS,
        }
        for prop_name, options in select_props.items():
            if prop_name not in existing:
                updates[prop_name] = {
                    "select": {
                        "options": [{"name": o} for o in options]
                    }
                }

        if PROP["risk_flag"] not in existing:
            updates[PROP["risk_flag"]] = {"checkbox": {}}

        if updates:
            notion_client.databases.update(
                database_id=NOTION_TASK_DB_ID,
                properties=updates,
            )
            logger.info(f"DB 속성 추가 완료: {list(updates.keys())}")
        else:
            logger.info("DB 속성 이미 존재 — 스킵")

    except Exception as e:
        logger.error(f"DB 속성 확인/추가 실패: {e}")


def get_notion_user_id(slack_display_name: str) -> str | None:
    """인원 DB에서 Notion 사용자 ID를 가져옴."""
    try:
        user_info = _get_user_info_from_db(slack_display_name)
        return user_info.get("person_id")
    except Exception as e:
        logger.error(f"Notion 사용자 ID 조회 실패: {e}")
        return None


def ensure_log_db() -> str | None:
    """일지 DB 존재 확인, 없으면 생성. DB ID 반환."""
    global NOTION_LOG_DB_ID

    if NOTION_LOG_DB_ID and "your-log-db" not in NOTION_LOG_DB_ID:
        _ensure_log_db_properties()
        return NOTION_LOG_DB_ID

    if not NOTION_TASK_DB_ID:
        logger.warning("NOTION_TASK_DB_ID가 없어 일지 DB 생성 불가.")
        return None

    try:
        # 업무 DB의 부모 페이지를 그대로 사용
        task_db = notion_client.databases.retrieve(database_id=NOTION_TASK_DB_ID)
        parent = task_db.get("parent", {})

        new_db = notion_client.databases.create(
            parent=parent,
            title=[{"type": "text", "text": {"content": "📋 일지 DB"}}],
            properties=LOG_DB_PROPERTIES,
        )
        new_id = new_db["id"]
        logger.info(f"일지 DB 생성 완료: {new_id}")
        logger.warning(f"⚠️ NOTION_LOG_DB_ID={new_id} 를 환경변수에 등록하세요")
        NOTION_LOG_DB_ID = new_id
        return new_id

    except Exception as e:
        logger.error(f"일지 DB 생성 실패: {e}")
        return None


def _ensure_log_db_properties():
    """일지 DB에 필요한 속성이 있는지 확인, 없으면 추가."""
    try:
        db = notion_client.databases.retrieve(database_id=NOTION_LOG_DB_ID)
        existing = db.get("properties", {})

        updates = {}
        # 1. 텍스트 속성들
        required_rich_text = ["완료", "내일예정", "협의사항", "이슈", "리스크"]
        for prop_name in required_rich_text:
            if prop_name not in existing:
                updates[prop_name] = {"rich_text": {}}

        # 2. 날짜/작성자/카테고리/연결Task
        if "날짜" not in existing:
            updates["날짜"] = {"date": {}}
        if "작성자" not in existing:
            updates["작성자"] = {"people": {}}
        if "카테고리" not in existing:
            updates["카테고리"] = LOG_DB_PROPERTIES["카테고리"]
        if "연결Task" not in existing:
            updates["연결Task"] = LOG_DB_PROPERTIES["연결Task"]

        if updates:
            notion_client.databases.update(
                database_id=NOTION_LOG_DB_ID,
                properties=updates,
            )
            logger.info(f"일지 DB 속성 강제 업데이트 완료: {list(updates.keys())}")
        else:
            logger.info("일지 DB 속성 모두 확인됨")

    except Exception as e:
        logger.error(f"일지 DB 속성 확인/추가 실패 (ID: {NOTION_LOG_DB_ID}): {e}")


def _build_active_task_filter() -> dict:
    """완료/보류 제외 필터."""
    return {
        "and": [
            {"property": PROP["status"], "status": {"does_not_equal": s}}
            for s in EXCLUDE_STATUS
        ]
    }


def _parse_task(page: dict) -> dict:
    """노션 페이지 → 공통 Task dict 변환."""
    props = page["properties"]

    title_list = props.get(PROP["title"], {}).get("title", [])
    name = title_list[0]["plain_text"] if title_list else "(제목 없음)"

    deadline_raw = props.get(PROP["deadline"], {}).get("date")
    deadline = deadline_raw["start"] if deadline_raw else None

    status_raw = props.get(PROP["status"], {}).get("status")
    status = status_raw["name"] if status_raw else None

    client_raw = props.get(PROP["client"], {}).get("select")
    client = client_raw["name"] if client_raw else None

    phase_raw = props.get(PROP["phase"], {}).get("select")
    phase = phase_raw["name"] if phase_raw else None

    assignees = props.get(PROP["assignee"], {}).get("people", [])
    assignee_names = [p.get("name", "") for p in assignees]

    risk_flag = props.get(PROP["risk_flag"], {}).get("checkbox", False)

    return {
        "id":        page["id"],
        "name":      name,
        "deadline":  deadline,
        "status":    status,
        "client":    client,
        "phase":     phase,
        "assignees": assignee_names,
        "risk_flag": risk_flag,
        "url":       page["url"],
    }


def _get_user_info_from_db(name: str) -> dict:
    """인원 DB에서 사용자의 정식 성함, 모든 호칭, 사람 ID를 가져옴."""
    if not NOTION_USER_DB_ID:
        return {"name": name, "aliases": [name], "person_id": None}

    cache_key = f"user_info:{name}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    try:
        response = notion_client.databases.query(
            database_id=NOTION_USER_DB_ID,
            filter={
                "or": [
                    {"property": PROP_USER["name"], "title":     {"contains": name}},
                    {"property": PROP_USER["alias"], "rich_text": {"contains": name}},
                ]
            }
        )

        if not response["results"]:
            return {"name": name, "aliases": [name], "person_id": None}

        page  = response["results"][0]
        props = page["properties"]

        db_name = props[PROP_USER["name"]]["title"][0]["plain_text"]

        alias_raw  = props[PROP_USER["alias"]]["rich_text"]
        aliases_str = alias_raw[0]["plain_text"] if alias_raw else ""
        aliases = [a.strip() for a in aliases_str.replace(" 등", "").replace(" 등등", "").split(",") if a.strip()]
        aliases.append(db_name)

        person_list = props[PROP_USER["person"]]["people"]
        person_id   = person_list[0]["id"] if person_list else None

        result = {"name": db_name, "aliases": list(set(aliases)), "person_id": person_id}
        cache_set(cache_key, result, ttl=300)  # 5분 캐시
        return result

    except Exception as e:
        logger.error(f"인원 DB 조회 실패: {e}")
        return {"name": name, "aliases": [name], "person_id": None}


def get_my_tasks(slack_display_name: str) -> list[dict]:
    """
    내 전용 업무 및 정제되지 않은 미배정 업무를 우선순위에 따라 조회.
    성함뿐만 아니라 인원 DB에 등록된 '호칭'을 모두 사용하여 검색.
    """
    try:
        user_info  = _get_user_info_from_db(slack_display_name)
        my_keywords = [k.lower() for k in user_info["aliases"]]

        response = notion_client.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter=_build_active_task_filter(),
            sorts=[{"property": PROP["deadline"], "direction": "ascending"}],
            page_size=100
        )

        my_assigned            = []
        name_matched_unassigned = []
        other_unassigned        = []

        for page in response["results"]:
            task      = _parse_task(page)
            assignees = task.get("assignees") or []

            if assignees:
                if any(any(kw in n.lower() for kw in my_keywords) for n in assignees):
                    task["is_assigned"] = True
                    my_assigned.append(task)
                continue

            task["is_assigned"] = False
            if any(kw in task["name"].lower() for kw in my_keywords):
                name_matched_unassigned.append(task)
            else:
                other_unassigned.append(task)

        tasks = my_assigned + name_matched_unassigned + other_unassigned
        logger.info(f"Task 조회 완료: 총 {len(tasks)}개 (매칭 키워드: {my_keywords})")
        return tasks

    except Exception as e:
        logger.error(f"노션 Task 조회 실패: {e}")
        return []


def update_task_assignee(page_id: str, slack_display_name: str) -> bool:
    """Task의 담당자(People) 필드가 비어있는 경우 현재 사용자로 업데이트합니다."""
    try:
        page     = notion_client.pages.retrieve(page_id=page_id)
        props    = page.get("properties", {})
        existing = props.get(PROP["assignee"], {}).get("people", [])

        if existing:
            return True

        user_id = get_notion_user_id(slack_display_name)
        if not user_id:
            logger.warning(f"노션 사용자를 찾을 수 없음: {slack_display_name}")
            return False

        notion_client.pages.update(
            page_id=page_id,
            properties={PROP["assignee"]: {"people": [{"id": user_id}]}},
        )
        logger.info(f"Task 담당자 자동 지정 완료: {page_id} → {slack_display_name}")
        return True
    except Exception as e:
        logger.error(f"Task 담당자 자동 지정 실패: {e}")
        return False


def search_tasks(keyword: str, slack_display_name: str = None) -> list[dict]:
    """키워드로 전체 Task DB 검색. 활성 업무(완료/보류 제외)만 반환."""
    try:
        active_filter = _build_active_task_filter()
        final_filter  = {
            "and": [
                *active_filter["and"],
                {"property": PROP["title"], "title": {"contains": keyword}}
            ]
        }

        response = notion_client.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter=final_filter,
            sorts=[{"property": PROP["deadline"], "direction": "ascending"}],
            page_size=100
        )

        my_keywords = []
        if slack_display_name:
            user_info = _get_user_info_from_db(slack_display_name)
            my_keywords = [k.lower() for k in user_info["aliases"]]

        tasks = []
        for page in response["results"]:
            task = _parse_task(page)
            if my_keywords:
                assignees = task.get("assignees") or []
                if assignees:
                    if any(any(kw in n.lower() for kw in my_keywords) for n in assignees):
                        task["is_assigned"] = True
                    else:
                        task["is_assigned"] = False
                else:
                    # 미배정 중 업무명에 키워드 포함 시 임시로 본인 업무로 고려할지 여부 (get_my_tasks와 동일 로직)
                    if any(kw in task["name"].lower() for kw in my_keywords):
                        task["is_assigned"] = True
                    else:
                        task["is_assigned"] = False
            else:
                task["is_assigned"] = (len(task.get("assignees") or []) > 0) # 단순 배정 여부만 표시

            tasks.append(task)

        logger.info(f"Task 검색 '{keyword}': {len(tasks)}개 (사용자: {slack_display_name})")
        return tasks

    except Exception as e:
        logger.error(f"Task 검색 실패: {e}")
        return []


def get_all_tasks() -> list[dict]:
    try:
        response = notion_client.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter=_build_active_task_filter(),
            sorts=[{"property": PROP["deadline"], "direction": "ascending"}],
            page_size=100
        )
        return [_parse_task(page) for page in response["results"]]

    except Exception as e:
        logger.error(f"전체 Task 조회 실패: {e}")
        return []


def create_task(task_name: str, assignee_notion_id: str = None,
                deadline: str = None, client_name: str = None,
                phase: str = None) -> dict | None:
    properties = {
        PROP["title"]: {
            "title": [{"text": {"content": task_name}}]
        },
        PROP["status"]: {
            "status": {"name": "🙏 진행 예정"}
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
        title_list = page["properties"][PROP["title"]]["title"]
        name = title_list[0]["plain_text"] if title_list else task_name
        logger.info(f"새 Task 생성: {name}")
        return {"id": page["id"], "name": name, "url": page["url"]}

    except Exception as e:
        logger.error(f"Task 생성 실패: {e}")
        return None


def update_task_status(page_id: str, status_name: str) -> bool:
    """Task의 진행 상황(Status) 속성을 업데이트합니다."""
    if status_name not in STATUS_OPTIONS:
        logger.error(f"잘못된 상태값: {status_name}")
        return False
    try:
        notion_client.pages.update(
            page_id=page_id,
            properties={PROP["status"]: {"status": {"name": status_name}}},
        )
        logger.info(f"Task 상태 업데이트 완료: {page_id} → {status_name}")
        return True
    except Exception as e:
        logger.error(f"Task 상태 업데이트 실패 ({page_id}): {e}")
        return False


def _paragraph_block(text: str) -> dict:
    """단일 paragraph 블록 생성."""
    return {
        "object": "block",
        "type":   "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
        }
    }


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
    """일지 DB에 새 페이지를 생성합니다."""
    log_db_id = ensure_log_db()
    if not log_db_id:
        logger.error("일지 DB ID를 가져올 수 없습니다.")
        return None

    # 제목: 날짜 + 업무명
    title_text = f"{log_date} | {task_name}"

    properties = {
        "일지내용": {
            "title": [{"text": {"content": title_text}}]
        },
        "날짜": {"date": {"start": log_date}},
    }

    # 연결Task
    if task_id and task_id != "NEW_TASK":
        properties["연결Task"] = {
            "relation": [{"id": task_id}]
        }

    # 작성자 (Notion Person)
    author_id = get_notion_user_id(author_slack) if author_slack else None
    if author_id:
        properties["작성자"] = {"people": [{"id": author_id}]}

    # 텍스트 속성
    if completed:
        properties["완료"]     = {"rich_text": [{"text": {"content": completed[:2000]}}]}
    if tomorrow:
        properties["내일예정"] = {"rich_text": [{"text": {"content": tomorrow[:2000]}}]}
    if consultation:
        properties["협의사항"] = {"rich_text": [{"text": {"content": consultation[:2000]}}]}
    if issues:
        properties["이슈"]     = {"rich_text": [{"text": {"content": issues[:2000]}}]}
    if risk:
        properties["리스크"]   = {"rich_text": [{"text": {"content": risk[:2000]}}]}

    # 카테고리 태그
    categories = []
    if completed:   categories.append({"name": "완료"})
    if tomorrow:    categories.append({"name": "예정"})
    if consultation: categories.append({"name": "협의"})
    if issues:      categories.append({"name": "이슈"})
    if risk:        categories.append({"name": "리스크"})
    if categories:
        properties["카테고리"] = {"multi_select": categories}

    try:
        page = notion_client.pages.create(
            parent={"database_id": log_db_id},
            properties=properties,
        )
        logger.info(f"일지 블록 추가 완료: {page['id']}")

        # 본문 블록 추가
        blocks = []
        for header, text in [
            ("✅ 오늘 완료", completed),
            ("🔜 내일 예정", tomorrow),
            ("🤝 협의/보고", consultation),
            ("⚠️ 이슈/결정", issues),
            ("🚨 마감 리스크", risk),
        ]:
            if text:
                blocks.append({
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {
                        "rich_text": [{"type": "text", "text": {"content": header}}]
                    }
                })
                blocks.append(_paragraph_block(text))

        if blocks:
            # 1. 일지 DB 페이지 내부에 블록 추가
            notion_client.blocks.children.append(block_id=page["id"], children=blocks)
            
            # 2. 기존 Task 페이지 본문에도 히스토리로 블록 추가 (여기가 핵심!)
            if task_id and task_id != "NEW_TASK":
                # 구분선과 날짜 헤더 추가
                history_header = [
                    {"type": "divider", "divider": {}},
                    {
                        "type": "heading_2",
                        "heading_2": {
                            "rich_text": [{"type": "text", "text": {"content": f"📅 {log_date} 업무일지 ({author_slack})"}}]
                        }
                    }
                ]
                try:
                    notion_client.blocks.children.append(block_id=task_id, children=history_header + blocks)
                    logger.info(f"Task 페이지 히스토리 추가 완료: {task_id}")
                except Exception as te:
                    logger.warning(f"Task 페이지 히스토리 추가 실패 (무시): {te}")

        logger.info(f"일지 기록 완료: Log DB({page['id']}), Task({task_id})")

        # 상태 업데이트 및 담당자 지정
        if status_update and status_update in STATUS_OPTIONS:
            update_task_status(task_id, status_update)
        if task_id and task_id != "NEW_TASK" and author_slack:
            update_task_assignee(task_id, author_slack)

        return {"id": page["id"], "url": page["url"]}

    except Exception as e:
        logger.error(f"일지 DB 기록 실패: {e}")
        return None


def get_weekly_updated_tasks() -> list[dict]:
    """이번 주 업데이트된 Task 목록 조회."""
    try:
        one_week_ago = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
        response = notion_client.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter={
                "and": [
                    {"timestamp": "last_edited_time", "last_edited_time": {"after": one_week_ago}},
                    *_build_active_task_filter()["and"],
                ]
            },
            sorts=[{"property": PROP["deadline"], "direction": "ascending"}],
            page_size=50,
        )
        return [_parse_task(page) for page in response["results"]]

    except Exception as e:
        logger.error(f"주간 Task 조회 실패: {e}")
        return []


def get_task_todos(task_id: str) -> list[dict]:
    """Task 페이지의 To-do 항목을 재귀적으로 조회하여 반환.

    - 실제 to_do 블록 탐색
    - callout/toggle 등 컨테이너 내부도 탐색
    - paragraph/bulleted_list_item 내의 '- [ ] ' 패턴도 파싱
    """
    TODO_PATTERN = re.compile(r"^\s*-\s*\[(x| )\]\s*(.+)$", re.IGNORECASE)

    def _fetch(block_id: str, depth: int = 0) -> list[dict]:
        if depth > 3:
            return []
        todos = []
        try:
            resp = notion_client.blocks.children.list(block_id=block_id)
            for block in resp.get("results", []):
                btype = block.get("type", "")
                if btype == "to_do":
                    raw = block["to_do"]
                    text = "".join(rt["plain_text"] for rt in raw.get("rich_text", []))
                    if text:
                        todos.append({"id": block["id"], "text": text,
                                      "checked": raw.get("checked", False), "block_type": "to_do"})
                elif btype in ("paragraph", "bulleted_list_item", "numbered_list_item"):
                    raw_text = "".join(rt["plain_text"]
                                       for rt in block.get(btype, {}).get("rich_text", []))
                    for i, line in enumerate(raw_text.splitlines()):
                        m = TODO_PATTERN.match(line)
                        if m:
                            todos.append({"id": f"{block['id']}::line_{i}",
                                          "text": m.group(2).strip(),
                                          "checked": m.group(1).strip().lower() == "x",
                                          "block_type": "text_pattern"})
                    if block.get("has_children"):
                        todos.extend(_fetch(block["id"], depth + 1))
                elif btype in ("callout", "toggle", "quote", "column", "column_list"):
                    if block.get("has_children"):
                        todos.extend(_fetch(block["id"], depth + 1))
        except Exception as e:
            logger.warning(f"블록 조회 실패 (depth={depth}): {e}")
        return todos

    try:
        return _fetch(task_id)
    except Exception as e:
        logger.error(f"To-do 조회 실패 ({task_id}): {e}")
        return []


def update_todo_checked(block_id: str, checked: bool) -> bool:
    """To-do 블록 또는 텍스트 패턴의 체크 상태를 업데이트."""
    try:
        # 텍스트 패턴인 경우 (ID에 ::line_ 포함)
        if "::line_" in block_id:
            real_block_id, line_idx_str = block_id.split("::line_")
            line_idx = int(line_idx_str)
            
            # 블록의 전체 텍스트 가져오기
            block = notion_client.blocks.retrieve(block_id=real_block_id)
            btype = block.get("type", "")
            rich_texts = block.get(btype, {}).get("rich_text", [])
            
            # 각 라인을 돌며 해당 인덱스의 [ ]를 [x]로 변경
            new_rich_text = []
            for rt in rich_texts:
                content = rt.get("text", {}).get("content", "")
                lines = content.splitlines(keepends=True)
                
                # 매우 정교한 치환이 필요하지만 간단하게 해당 라인의 패턴 치환
                # (현 실무 구조상 한 블록에 rich_text가 하나인 경우가 대부분임)
                new_lines = []
                for i, line in enumerate(lines):
                    if i == line_idx:
                        if checked:
                            line = line.replace("- [ ]", "- [x]").replace("- [ ]", "- [x]")
                        else:
                            line = line.replace("- [x]", "- [ ]").replace("- [X]", "- [ ]")
                    new_lines.append(line)
                
                rt["text"]["content"] = "".join(new_lines)
                rt.pop("plain_text", None) # 업데이트 시 plain_text는 제거
                new_rich_text.append(rt)
                
            notion_client.blocks.update(block_id=real_block_id, **{btype: {"rich_text": new_rich_text}})
            return True
            
        # 일반 To-do 블록인 경우
        notion_client.blocks.update(block_id=block_id, **{"to_do": {"checked": checked}})
        return True
    except Exception as e:
        logger.error(f"To-do 업데이트 실패 ({block_id}): {e}")
        return False


# ── 하위 호환 별칭 (modal.py 등 기존 코드에서 append_daily_log로 호출) ──
append_daily_log = save_log


def get_handover_data(task_id: str) -> list[dict]:
    """특정 Task에 연결된 일지에서 이슈/리스크 추출."""
    if not NOTION_LOG_DB_ID or "your-log-db" in NOTION_LOG_DB_ID:
        return []
    try:
        response = notion_client.databases.query(
            database_id=NOTION_LOG_DB_ID,
            filter={"property": "연결Task", "relation": {"contains": task_id}},
            sorts=[{"property": "날짜", "direction": "ascending"}],
        )
        results = []
        for page in response["results"]:
            props = page["properties"]

            date_raw = props.get("날짜", {}).get("date")
            date_str = date_raw["start"] if date_raw else "날짜 없음"

            author_people = props.get("작성자", {}).get("people", [])
            author = author_people[0]["name"] if author_people else "미상"

            def _rt(key):
                rt = props.get(key, {}).get("rich_text", [])
                return rt[0]["plain_text"] if rt else ""

            issues = _rt("이슈")
            risk   = _rt("리스크")

            if issues or risk:
                results.append({
                    "date":   date_str,
                    "author": author,
                    "issues": issues,
                    "risk":   risk,
                })
        return results

    except Exception as e:
        logger.error(f"인수인계 데이터 조회 실패: {e}")
        return []
