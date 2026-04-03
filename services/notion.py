"""
services/notion.py — 노션 API 전담 모듈
"""

import datetime
import logging
import re
import time
from notion_client import Client
from config import NOTION_TOKEN, NOTION_TASK_DB_ID, NOTION_LOG_DB_ID, NOTION_USER_DB_ID

logger = logging.getLogger(__name__)

notion = Client(auth=NOTION_TOKEN)

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
    "name": "이름",
    "alias": "호칭",
    "person": "사람",
}

CLIENT_OPTIONS = ["청주시청", "괴산군청", "무주군청", "진천군청", "음성군청", "농어촌공사", "행정안전부", "나래공간", "기타"]
PHASE_OPTIONS = ["제안·입찰", "착수", "중간보고", "최종납품"]

EXCLUDE_STATUS = ["✅ 완료", "⏭ 보류"]
STATUS_OPTIONS = ["🙏 진행 예정", "🚀 진행 중", "💡 피드백", "⏭ 보류", "✅ 완료"]
DEADLINE_CUTOFF_DAYS = 7  # 마감일이 이 일수 이상 지난 Task는 제외

# 일지 DB 속성 정의 (Phase 3: DB 자동 생성용)
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
        db = notion.databases.retrieve(database_id=NOTION_TASK_DB_ID)
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

        # 마감리스크 체크박스 (뷰 5종 중 "마감 리스크 모음" 필터용)
        if PROP["risk_flag"] not in existing:
            updates[PROP["risk_flag"]] = {"checkbox": {}}

        if updates:
            notion.databases.update(
                database_id=NOTION_TASK_DB_ID,
                properties=updates,
            )
            logger.info(f"DB 속성 추가 완료: {list(updates.keys())}")
        else:
            logger.info("DB 속성 이미 존재 — 스킵")

    except Exception as e:
        logger.error(f"DB 속성 확인/추가 실패: {e}")


def ensure_log_db() -> str | None:
    """일지 DB 존재 확인, 없으면 생성. DB ID 반환."""
    global NOTION_LOG_DB_ID

    if NOTION_LOG_DB_ID and "your-log-db" not in NOTION_LOG_DB_ID:
        _ensure_log_db_properties()
        return NOTION_LOG_DB_ID

    # DB가 없으면 TASK DB와 같은 부모 아래에 생성
    try:
        task_db = notion.databases.retrieve(NOTION_TASK_DB_ID)
        parent = task_db.get("parent", {})
        parent_type = parent.get("type")

        if parent_type == "page_id":
            db_parent_page_id = parent["page_id"]
        elif parent_type == "workspace":
            db_parent_page_id = parent["workspace"]
        elif parent_type == "block_id":
            # TASK DB가 블록 안에 있는 경우: 컨테이너 페이지를 TASK DB 내에 생성
            container = notion.pages.create(
                parent={"database_id": NOTION_TASK_DB_ID},
                properties={
                    PROP["title"]: {"title": [{"text": {"content": "📋 일지 시스템 (자동생성-삭제금지)"}}]},
                    PROP["status"]: {"status": {"name": "✅ 완료"}},
                },
            )
            db_parent_page_id = container["id"]
            logger.info(f"일지 DB 컨테이너 페이지 생성: {db_parent_page_id}")
        else:
            logger.error(f"지원하지 않는 parent 타입: {parent_type}")
            return None

        new_db = notion.databases.create(
            parent={"type": "page_id", "page_id": db_parent_page_id},
            title=[{"type": "text", "text": {"content": "📋 일지 DB"}}],
            properties=LOG_DB_PROPERTIES,
        )
        NOTION_LOG_DB_ID = new_db["id"]
        logger.info(f"일지 DB 생성 완료: {NOTION_LOG_DB_ID}")
        logger.info(f"⚠️ NOTION_LOG_DB_ID={NOTION_LOG_DB_ID} 를 환경변수에 등록하세요")
        return NOTION_LOG_DB_ID
    except Exception as e:
        logger.error(f"일지 DB 생성 실패: {e}")
        return None


def _ensure_log_db_properties():
    """일지 DB에 필요한 속성이 있는지 확인, 없으면 추가."""
    try:
        db = notion.databases.retrieve(database_id=NOTION_LOG_DB_ID)
        existing = db.get("properties", {})

        updates = {}
        required_rich_text = ["완료", "내일예정", "협의사항", "이슈", "리스크"]
        for prop_name in required_rich_text:
            if prop_name not in existing:
                updates[prop_name] = {"rich_text": {}}

        if "카테고리" not in existing:
            updates["카테고리"] = LOG_DB_PROPERTIES["카테고리"]

        if updates:
            notion.databases.update(
                database_id=NOTION_LOG_DB_ID,
                properties=updates,
            )
            logger.info(f"일지 DB 속성 추가 완료: {list(updates.keys())}")
        else:
            logger.info("일지 DB 속성 이미 존재 — 스킵")

    except Exception as e:
        logger.error(f"일지 DB 속성 확인/추가 실패: {e}")


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
        "id": page["id"],
        "name": name,
        "deadline": deadline,
        "status": status,
        "client": client,
        "phase": phase,
        "assignees": assignee_names,
        "risk_flag": risk_flag,
        "url": page["url"],
    }


def _get_user_info_from_db(name: str) -> dict:
    """인원 DB에서 사용자의 정식 성함, 모든 호칭, 사람 ID를 가져옴."""
    if not NOTION_USER_DB_ID:
        return {"name": name, "aliases": [name], "person_id": None}

    try:
        # 이름 또는 호칭으로 검색
        response = notion.databases.query(
            database_id=NOTION_USER_DB_ID,
            filter={
                "or": [
                    {"property": PROP_USER["name"], "title": {"contains": name}},
                    {"property": PROP_USER["alias"], "rich_text": {"contains": name}},
                ]
            }
        )

        if not response["results"]:
            return {"name": name, "aliases": [name], "person_id": None}

        # 가장 유사한 첫 번째 결과 사용
        page = response["results"][0]
        props = page["properties"]
        
        db_name = props[PROP_USER["name"]]["title"][0]["plain_text"]
        
        alias_raw = props[PROP_USER["alias"]]["rich_text"]
        aliases_str = alias_raw[0]["plain_text"] if alias_raw else ""
        # "호칭1, 호칭2" 형태를 리스트로 분리
        aliases = [a.strip() for a in aliases_str.replace(" 등", "").replace(" 등등", "").split(",") if a.strip()]
        aliases.append(db_name) # 정식 성함도 포함
        
        person_list = props[PROP_USER["person"]]["people"]
        person_id = person_list[0]["id"] if person_list else None
        
        return {"name": db_name, "aliases": list(set(aliases)), "person_id": person_id}

    except Exception as e:
        logger.error(f"인원 DB 조회 실패: {e}")
        return {"name": name, "aliases": [name], "person_id": None}


def get_my_tasks(slack_display_name: str) -> list[dict]:
    """
    내 전용 업무 및 정제되지 않은 미배정 업무를 우선순위에 따라 조회.
    성함뿐만 아니라 인원 DB에 등록된 '호칭'을 모두 사용하여 검색.
    """
    try:
        # 0. 인원 DB에서 내 별칭(호칭) 목록 가져오기
        user_info = _get_user_info_from_db(slack_display_name)
        my_keywords = [k.lower() for k in user_info["aliases"]]

        # 1. 전체 진행 중인 Task 조회
        response = notion.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter=_build_active_task_filter(),
            sorts=[{"property": PROP["deadline"], "direction": "ascending"}],
            page_size=100
        )

        my_assigned = []
        name_matched_unassigned = []
        other_unassigned = []

        for page in response["results"]:
            task = _parse_task(page)
            assignees = task.get("assignees") or []
            
            # ── 1순위: 정식 담당자 매칭 (그간 등록된 이름들이 키워드 중 하나와 일치하면) ──
            if assignees:
                if any(any(kw in n.lower() for kw in my_keywords) for n in assignees):
                    task["is_assigned"] = True
                    my_assigned.append(task)
                continue

            # ── 2순위 & 3순위: 미배정 업무 ────────────────────
            task["is_assigned"] = False
            # 업무명에 내 이름 또는 호칭 중 하나라도 포함되어 있으면 2순위
            if any(kw in task["name"].lower() for kw in my_keywords):
                name_matched_unassigned.append(task)
            else:
                other_unassigned.append(task)

        # 우선순위에 따라 결합
        tasks = my_assigned + name_matched_unassigned + other_unassigned
        logger.info(f"Task 조회 완료: 총 {len(tasks)}개 (매칭 키워드: {my_keywords})")
        return tasks

    except Exception as e:
        logger.error(f"노션 Task 조회 실패: {e}")
        return []


def update_task_assignee(page_id: str, slack_display_name: str) -> bool:
    """Task의 담당자(People) 필드가 비어있는 경우 현재 사용자로 업데이트합니다."""
    try:
        # 현재 상태 확인 (중복 배정 방지)
        page = notion.pages.retrieve(page_id=page_id)
        props = page.get("properties", {})
        existing = props.get(PROP["assignee"], {}).get("people", [])
        
        if existing:
            return True

        user_id = get_notion_user_id(slack_display_name)
        if not user_id:
            logger.warning(f"노션 사용자를 찾을 수 없음: {slack_display_name}")
            return False

        notion.pages.update(
            page_id=page_id,
            properties={PROP["assignee"]: {"people": [{"id": user_id}]}},
        )
        logger.info(f"Task 담당자 자동 지정 완료: {page_id} → {slack_display_name}")
        return True
    except Exception as e:
        logger.error(f"Task 담당자 자동 지정 실패: {e}")
        return False


def search_tasks(keyword: str) -> list[dict]:
    """
    키워드로 전체 Task DB 검색. 필터 없이 업무명만으로 검색.
    """
    try:
        # 활성 업무 필터 추가 (완료/보류된 업무 전면 제외)
        active_filter = _build_active_task_filter()
        final_filter = {
            "and": [
                *active_filter["and"],
                {"property": PROP["title"], "title": {"contains": keyword}}
            ]
        }
        
        response = notion.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter=final_filter,
            sorts=[{"property": PROP["deadline"], "direction": "ascending"}],
            page_size=100
        )

        tasks = [_parse_task(page) for page in response["results"]]
        logger.info(f"Task 필터링 검색 '{keyword}': {len(tasks)}개")
        return tasks

    except Exception as e:
        logger.error(f"Task 검색 실패: {e}")
        return []


def get_all_tasks() -> list[dict]:
    try:
        response = notion.databases.query(
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
        page = notion.pages.create(
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
        notion.pages.update(
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
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _build_log_blocks(log: dict, today_str: str) -> list[dict]:
    """일지 블록 생성 — 토글 헤딩 + 조건부 카테고리."""
    date_str = log.get("log_date") or today_str
    author = log.get("author", "")

    completed = log.get("completed", "").strip()
    tomorrow = log.get("tomorrow", "").strip()
    consultation = log.get("consultation", "").strip()
    issues = log.get("issues", "").strip()
    risk = log.get("risk", "").strip()

    # 요약 라인 구성
    def _count(text):
        if not text or text == "-":
            return 0
        return len([l for l in text.split("\n") if l.strip() and l.strip() != "-"])

    summary_parts = []
    if _count(completed):
        summary_parts.append(f"✅ {_count(completed)}건")
    if _count(tomorrow):
        summary_parts.append(f"🔜 {_count(tomorrow)}건")
    if _count(issues):
        summary_parts.append(f"⚠️ {_count(issues)}건")
    if _count(risk):
        summary_parts.append(f"🚨 {_count(risk)}건")

    summary = " · ".join(summary_parts) if summary_parts else ""

    heading_text = f"📅 {date_str}  {author}"
    if date_str != today_str:
        heading_text += f"  (작성: {today_str})"
    if summary:
        heading_text += f" — {summary}"

    # 하위 블록 (토글 내부)
    children = [
        _paragraph_block(f"✅ 완료\n{completed or '-'}"),
        _paragraph_block(f"🔜 내일 예정\n{tomorrow or '-'}"),
    ]
    if consultation and consultation != "-":
        children.append(_paragraph_block(f"🤝 협의/보고\n{consultation}"))
    if issues and issues != "-":
        children.append(_paragraph_block(f"⚠️ 이슈/결정사항\n{issues}"))
    if risk and risk != "-":
        children.append(_paragraph_block(f"🚨 마감 리스크\n{risk}"))

    return [
        {"object": "block", "type": "divider", "divider": {}},
        {
            "object": "block", "type": "heading_3",
            "heading_3": {
                "rich_text": [{"type": "text", "text": {"content": heading_text}}],
                "is_toggleable": True,
                "children": children,
            },
        },
    ]


def append_daily_log(page_id: str, log: dict, task_name: str = "") -> bool:
    """일지 블록을 페이지 최상단에 추가 (position:start, 기존 블록 보존)."""
    today = datetime.date.today().strftime("%Y-%m-%d")

    # 마감리스크 체크박스 자동 업데이트
    risk = (log.get("risk") or "").strip()
    has_risk = risk not in ("-", "", None) and bool(risk)
    try:
        notion.pages.update(
            page_id=page_id,
            properties={PROP["risk_flag"]: {"checkbox": has_risk}},
        )
    except Exception as e:
        logger.warning(f"마감리스크 플래그 업데이트 실패 (무시): {e}")

    try:
        new_blocks = _build_log_blocks(log, today)
        notion.blocks.children.append(
            block_id=page_id,
            children=new_blocks,
            position={"type": "start"},
        )
        logger.info(f"일지 블록 추가 완료 (prepend): {page_id}")

        # 일지 DB에도 행 추가 (NOTION_LOG_DB_ID가 설정된 경우)
        if NOTION_LOG_DB_ID and task_name:
            create_daily_log_entry(page_id, task_name, log)

        return True

    except Exception as e:
        logger.error(f"일지 블록 추가 실패: {e}")
        return False


def get_notion_user_id(name: str) -> str | None:
    """성함 또는 슬랙 실명을 바탕으로 인원 DB 또는 전체 사용자 목록에서 ID 조회."""
    # 1. 인원 DB에서 먼저 조회 (가장 정확)
    user_info = _get_user_info_from_db(name)
    if user_info["person_id"]:
        return user_info["person_id"]

    # 2. 실패 시 기존 전체 사용자 목록 검색 (Fallback)
    try:
        users = notion.users.list()
        for user in users["results"]:
            if user.get("type") == "person" and name in user.get("name", ""):
                return user["id"]
        return None
    except Exception as e:
        logger.error(f"노션 사용자 조회 실패(Fallback): {e}")
        return None


# ════════════════════════════════════════════════════════════
# 일지 전용 DB 기록 (Phase 2)
# ════════════════════════════════════════════════════════════

def _has_content(text: str | None) -> bool:
    """빈 값 / '-' 을 내용 없음으로 판단."""
    return bool(text) and text.strip() not in ("", "-")


def _rich_text_prop(text: str) -> dict:
    """rich_text 속성 값 생성."""
    return {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}


def create_daily_log_entry(task_id: str, task_name: str, log: dict) -> dict | None:
    """일지 DB에 한 행 생성 (TASK DB 본문 기록과 병행)."""
    if not NOTION_LOG_DB_ID:
        return None

    # 카테고리 자동 판별
    categories = []
    if _has_content(log.get("completed")):
        categories.append("완료")
    if _has_content(log.get("tomorrow")):
        categories.append("예정")
    if _has_content(log.get("consultation")):
        categories.append("협의")
    if _has_content(log.get("issues")):
        categories.append("이슈")
    if _has_content(log.get("risk")):
        categories.append("리스크")

    # 제목: Task 이름 (완료 내용은 "완료" 속성에 별도 저장)
    title = task_name

    today_str = datetime.date.today().strftime("%Y-%m-%d")

    properties = {
        "일지내용": {"title": [{"text": {"content": title}}]},
        "날짜": {"date": {"start": log.get("log_date") or today_str}},
        "연결Task": {"relation": [{"id": task_id}]},
        "카테고리": {"multi_select": [{"name": c} for c in categories]},
    }

    # 작성자 (Notion person ID가 있으면)
    author_id = get_notion_user_id(log.get("author", ""))
    if author_id:
        properties["작성자"] = {"people": [{"id": author_id}]}

    # 선택 필드 (내용이 있을 때만)
    if _has_content(log.get("completed")):
        properties["완료"] = _rich_text_prop(log["completed"])
    if _has_content(log.get("tomorrow")):
        properties["내일예정"] = _rich_text_prop(log["tomorrow"])
    if _has_content(log.get("consultation")):
        properties["협의사항"] = _rich_text_prop(log["consultation"])
    if _has_content(log.get("issues")):
        properties["이슈"] = _rich_text_prop(log["issues"])
    if _has_content(log.get("risk")):
        properties["리스크"] = _rich_text_prop(log["risk"])

    try:
        page = notion.pages.create(
            parent={"database_id": NOTION_LOG_DB_ID},
            properties=properties,
        )
        logger.info(f"일지 DB 기록 완료: {page['id']}")
        return {"id": page["id"], "url": page["url"]}
    except Exception as e:
        logger.error(f"일지 DB 기록 실패: {e}")
        return None


# ════════════════════════════════════════════════════════════
# 주간 요약 조회
# ════════════════════════════════════════════════════════════

def _get_week_start() -> datetime.date:
    """이번 주 월요일 날짜 반환."""
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _get_weekly_logs(page_id: str, week_start: datetime.date) -> list[str]:
    """
    페이지의 child blocks에서 이번 주 일지를 파싱.
    heading_3의 "📅 YYYY-MM-DD" 패턴으로 날짜 추출.
    플랫 구조(기존)와 토글 구조(신규) 모두 지원.
    """
    week_end = week_start + datetime.timedelta(days=6)
    logs = []
    current_date = None
    current_lines = []
    cursor = None
    date_pattern = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")

    while True:
        kwargs = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor

        try:
            response = notion.blocks.children.list(**kwargs)
        except Exception as e:
            logger.error(f"블록 조회 실패 ({page_id}): {e}")
            break

        for block in response.get("results", []):
            btype = block.get("type")

            if btype == "heading_3":
                rich = block["heading_3"].get("rich_text", [])
                text = rich[0]["plain_text"] if rich else ""
                m = date_pattern.search(text)
                if m:
                    # 이전 날짜 블록 저장
                    if current_date and week_start <= current_date <= week_end:
                        logs.append("\n".join(current_lines))
                    current_date = datetime.date.fromisoformat(m.group(1))
                    current_lines = [text]

                    # 토글 헤딩: 이번 주 범위면 children에서 paragraph 읽기
                    if block.get("has_children") and week_start <= current_date <= week_end:
                        time.sleep(0.35)
                        try:
                            ch_resp = notion.blocks.children.list(block_id=block["id"])
                            for child in ch_resp.get("results", []):
                                if child.get("type") == "paragraph":
                                    ch_rich = child["paragraph"].get("rich_text", [])
                                    line = "".join(r.get("plain_text", "") for r in ch_rich)
                                    if line.strip():
                                        current_lines.append(line)
                        except Exception as e:
                            logger.warning(f"토글 children 조회 실패: {e}")
                    continue

            # 플랫 구조 fallback: sibling paragraph 읽기
            if current_date and btype == "paragraph":
                rich = block["paragraph"].get("rich_text", [])
                line = "".join(r.get("plain_text", "") for r in rich)
                if line.strip():
                    current_lines.append(line)

        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    # 마지막 블록 처리
    if current_date and week_start <= current_date <= week_end:
        logs.append("\n".join(current_lines))

    return logs


def get_handover_data(page_id: str) -> list[dict]:
    """
    페이지의 전체 일지에서 이슈/결정사항과 마감 리스크만 추출.
    반환: [{"date": "...", "author": "...", "issues": "...", "risk": "..."}, ...]
    (최신 일지가 위에 저장되므로 날짜 내림차순)
    내용 없는 항목(둘 다 없음)은 제외.
    플랫 구조(기존)와 토글 구조(신규) 모두 지원.
    """
    entries = []
    current_date = None
    current_author = ""
    current_issues = None
    current_risk = None
    cursor = None
    date_pattern = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})\s*(.*)")

    def _save_entry():
        nonlocal current_date, current_issues, current_risk
        if current_date and (current_issues or current_risk):
            entries.append({
                "date": current_date,
                "author": current_author,
                "issues": current_issues,
                "risk": current_risk,
            })

    def _extract_issues_risk(blocks_list):
        """paragraph 목록에서 이슈/리스크 추출."""
        nonlocal current_issues, current_risk
        for blk in blocks_list:
            if blk.get("type") != "paragraph":
                continue
            rich = blk["paragraph"].get("rich_text", [])
            line = "".join(r.get("plain_text", "") for r in rich)

            if line.startswith("⚠️ 이슈/결정사항"):
                parts = line.split("\n", 1)
                content = parts[1].strip() if len(parts) > 1 else ""
                if content and content != "-":
                    current_issues = content
            elif line.startswith("🚨 마감 리스크"):
                parts = line.split("\n", 1)
                content = parts[1].strip() if len(parts) > 1 else ""
                if content and content != "-":
                    current_risk = content

    while True:
        kwargs = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor

        try:
            response = notion.blocks.children.list(**kwargs)
        except Exception as e:
            logger.error(f"인수인계 블록 조회 실패 ({page_id}): {e}")
            break

        for block in response.get("results", []):
            btype = block.get("type")

            if btype == "heading_3":
                _save_entry()

                rich = block["heading_3"].get("rich_text", [])
                text = rich[0]["plain_text"] if rich else ""
                m = date_pattern.search(text)
                if m:
                    current_date = m.group(1)
                    author_raw = m.group(2).strip()
                    # 요약 카운트 제거: "홍길동 — ✅ 1건" → "홍길동"
                    current_author = author_raw.split("(")[0].split("—")[0].strip()
                    current_issues = None
                    current_risk = None

                    # 토글 헤딩: children에서 이슈/리스크 추출
                    if block.get("has_children"):
                        time.sleep(0.35)
                        try:
                            ch_resp = notion.blocks.children.list(block_id=block["id"])
                            _extract_issues_risk(ch_resp.get("results", []))
                        except Exception as e:
                            logger.warning(f"토글 children 조회 실패: {e}")
                else:
                    current_date = None
                continue

            # 플랫 구조 fallback
            if current_date and btype == "paragraph":
                _extract_issues_risk([block])

        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    _save_entry()
    return entries


def get_weekly_updated_tasks() -> list[dict]:
    """이번 주 수정된 활성 Task + 주간 일지 조회."""
    week_start = _get_week_start()
    week_start_iso = week_start.isoformat()

    try:
        response = notion.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter={
                "and": [
                    *_build_active_task_filter()["and"],
                    {
                        "timestamp": "last_edited_time",
                        "last_edited_time": {"on_or_after": week_start_iso},
                    },
                ]
            },
            sorts=[{"property": PROP["deadline"], "direction": "ascending"}],
            page_size=100,
        )

        tasks = []
        for page in response["results"]:
            task = _parse_task(page)
            time.sleep(0.35)  # API 속도 제한 방지
            task["weekly_logs"] = _get_weekly_logs(page["id"], week_start)
            tasks.append(task)

        logger.info(f"주간 업데이트 Task: {len(tasks)}개")
        return tasks

    except Exception as e:
        logger.error(f"주간 Task 조회 실패: {e}")
        return []
