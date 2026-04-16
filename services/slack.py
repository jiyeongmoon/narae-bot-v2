"""
services/slack.py — 슬랙 모달·메시지 블록 빌더
=================================================
"""

import datetime
import json

from services.notion import CLIENT_OPTIONS, PHASE_OPTIONS, CLIENT_TO_PREFIX, get_client_options_from_notion


# ════════════════════════════════════════════════════════════
# 1. Task 선택 모달
# ════════════════════════════════════════════════════════════

def _task_label(task: dict) -> str:
    """Task 목록 라벨: [상태] 업무명 (발주처, 단계, ~마감일) — 75자 제한."""
    # 1. 상태 라벨 결정
    prefix = "[✅내업무] " if task.get("is_assigned") else "[⚠️미배정] "
    
    # 2. 부가 정보 (입찰처, 단계 등) 구성
    parts = []
    if task.get("client"):
        parts.append(task["client"])
    if task.get("phase"):
        parts.append(task["phase"])
    if task.get("deadline"):
        parts.append(f"~{task['deadline']}")
    
    suffix = f" ({', '.join(parts)})" if parts else ""
    name = task["name"]
    
    # 3. 전체 길이 조절 (75자 제한)
    full_label = f"{prefix}{name}{suffix}"
    if len(full_label) > 75:
        # 가용 공간 = 75 - prefix 길이 - suffix 길이 - 말줄임표(3)
        available = 75 - len(prefix) - len(suffix) - 3
        if available > 5:
            name = f"{name[:available]}..."
        else:
            # 공간이 너무 부족하면 그냥 자름
            return full_label[:72] + "..."
    
    return f"{prefix}{name}{suffix}"


def _group_by_person(tasks: list[dict]) -> dict:
    """Task 목록을 담당자(assignees) 기준으로 그룹화합니다.
    
    Returns:
        {담당자명: [task, ...], ..., "미배정": [task, ...]}
    """
    grouped = {}
    for task in tasks:
        assignees = task.get("assignees") or []
        if not assignees:
            grouped.setdefault("미배정", []).append(task)
        else:
            for name in assignees:
                grouped.setdefault(name, []).append(task)
    return grouped


def build_task_select_modal(tasks: list[dict],
                            user_real_name: str = "",
                            search_keyword: str = "",
                            filter_user_id: str = None,
                            filter_user_name: str = "") -> dict:
    """활성 Task를 [담당자별] 섹션으로 분류하여 표시."""


    # ── 필터링 및 정렬 ────────────────
    ACTIVE_STATUSES = ["🚀 진행 중", "🙏 진행 예정"]
    all_active = [t for t in tasks if t.get("status") in ACTIVE_STATUSES]
    
    # 모든 태스크를 생성일 역순으로 미리 정렬
    all_active.sort(key=lambda x: x.get("created_time", ""), reverse=True)

    # 담당자별 그룹화 (정렬된 순서 유지)
    grouped = _group_by_person(all_active)

    # 본인 업무 (정렬 유지됨)
    my_tasks = [t for t in all_active if t.get("is_assigned")]
    
    # 미배정 업무 (정렬 유지됨)
    unassigned_tasks = grouped.get("미배정", [])

    # 타인 업무 (정렬 유지됨)
    other_groups = {name: tks for name, tks in grouped.items() if name != "미배정" and not any(t.get("is_assigned") for t in tks)}

    def _make_option(task: dict) -> dict:
        name = task["name"]
        parts = []
        if task.get("client"):   parts.append(task["client"])
        if task.get("deadline"): parts.append(f"~{task['deadline']}")
        
        # 담당자가 본인이 아닌 경우 이름 표시
        assignees = task.get("assignees")
        is_assigned = task.get("is_assigned")
        if assignees and not is_assigned:
            parts.append(f"담당: {', '.join(assignees)}")

        suffix = f" ({', '.join(parts)})" if parts else ""
        label  = f"{name}{suffix}"
        if len(label) > 74:
            label = label[:71] + "..."
        return {
            "text":  {"type": "plain_text", "text": label},
            "value": json.dumps({"id": task["id"], "status": task.get("status", "")}, ensure_ascii=False),
        }

    if search_keyword:
        guide_text = f"🔍 *\"{search_keyword}\"* 검색 결과 (진행 중/예정만 표시)"
    elif filter_user_id:
        guide_text = "👤 *담당자 필터링* 결과입니다."
    else:
        guide_text = "일지를 작성할 Task를 선택하세요. (복수 선택 가능)"

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": guide_text},
        },
        {
            "type": "section",
            "block_id": "block_filter_assignee",
            "text": {"type": "mrkdwn", "text": "👤 *담당자 필터*"},
            "accessory": {
                "type": "users_select",
                "action_id": "filter_assignee",
                "placeholder": {"type": "plain_text", "text": "팀원 선택 → 해당 담당자 업무 확인"},
            }
        },
        {
            "type": "input",
            "block_id": "block_search",
            "dispatch_action": True,
            "optional": True,
            "label": {"type": "plain_text", "text": "🔍 키워드 검색 (Enter) (옵션)"},
            "element": {
                "type": "plain_text_input",
                "action_id": "search_keyword",
                "placeholder": {"type": "plain_text", "text": "키워드 입력 후 Enter → 검색 결과로 갱신"},
                "dispatch_action_config": {
                    "trigger_actions_on": ["on_enter_pressed"]
                },
            }
        },
        # ── 새 Task 생성 (원하는 수만큼 입력) ───────────────────
        {"type": "divider"},
        {
            "type": "input",
            "block_id": "block_new_task_select",
            "optional": True,
            "label": {"type": "plain_text", "text": "➕ 새 Task 생성 (선택 사항)"},
            "hint": {"type": "plain_text", "text": "생성할 Task 개수를 입력해 주세요. 입력하지 않으면 새 Task를 만들지 않습니다."},
            "element": {
                "type": "number_input",
                "action_id": "new_task_count",
                "is_decimal_allowed": False,
                "min_value": "1",
                "max_value": "10",
                "placeholder": {"type": "plain_text", "text": "생성할 Task 수 (1~10)"},
            }
        },
    ]

    # self_header (필터링 시 해당 담당자 이름, 아니면 본인 이름)
    header_name = filter_user_name if filter_user_id and filter_user_name else user_real_name
    self_header = f"✅ *{header_name}* 님 담당 업무" if header_name else "✅ *내 업무*"

    # ── 내 업무 섹션 (제한 없음) ──────────────────────────────
    if my_tasks:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": self_header},
        })
        blocks.append({
            "type": "input",
            "block_id": "block_my_tasks",
            "optional": True,
            "label": {"type": "plain_text", "text": "내 업무 전체"},
            "element": {
                "type": "checkboxes",
                "action_id": "my_task_checkboxes",
                "options": [_make_option(t) for t in my_tasks],
            }
        })

    # ── 미배정 업무 섹션 (상위 5건) ──────────────────────────
    if unassigned_tasks:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "⚠️ *미배정 업무*"},
        })
        blocks.append({
            "type": "input",
            "block_id": "block_unassigned_tasks",
            "optional": True,
            "label": {"type": "plain_text", "text": "담당자 없음 (최대 5건)"},
            "element": {
                "type": "checkboxes",
                "action_id": "unassigned_task_checkboxes",
                "options": [_make_option(t) for t in unassigned_tasks[:5]],
            }
        })

    # ── 타인 업무 (담당자별 그룹화, 상위 5건) ──────────────────
    if other_groups:
        for person, person_tasks in other_groups.items():
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"👤 *{person}* 님의 업무"},
            })
            blocks.append({
                "type": "input",
                "block_id": f"block_other_{person}", # 고유 ID 생성
                "optional": True,
                "label": {"type": "plain_text", "text": f"{person} 담당 업무 (상위 5건)"},
                "element": {
                    "type": "checkboxes",
                    "action_id": "search_result_checkboxes", # 핸들러 호환성을 위해 동일 유지
                    "options": [_make_option(t) for t in person_tasks[:5]],
                }
            })

    if search_keyword and not (my_tasks or unassigned_tasks or other_groups):
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "검색 결과가 없습니다."},
        })

    # (새 Task 블록은 이미 위에서 검색 블록 바로 뒤에 추가됨)

    return {
        "type": "modal",
        "callback_id": "modal_task_select",
        "title": {"type": "plain_text", "text": "📝 업무일지 작성"},
        "submit": {"type": "plain_text", "text": "다음 →"},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": blocks,
    }


# ════════════════════════════════════════════════════════════
# 2. 일지 입력 모달
# ════════════════════════════════════════════════════════════

def build_log_step_modal(metadata_json: str, task_name: str,
                         step: int, total: int,
                         user_id: str = None,
                         is_new: bool = False,
                         current_status: str = None,
                         todos: list = None) -> dict:
    """단계별 일지 입력 모달. step/total로 진행 상태 표시."""
    from services.notion import STATUS_OPTIONS

    new_task_blocks = []
    if is_new:
        # Notion DB에서 발주처 옵션 실시간 로드 (실패 시 하드코딩 폴백)
        notion_client_opts = get_client_options_from_notion()
        client_options = [
            {"text": {"type": "plain_text", "text": c}, "value": c}
            for c in notion_client_opts
        ]
        phase_options = [
            {"text": {"type": "plain_text", "text": p}, "value": p}
            for p in PHASE_OPTIONS
        ]
        new_task_status_options = [
            {"text": {"type": "plain_text", "text": s}, "value": s}
            for s in STATUS_OPTIONS
        ]
        new_task_blocks = [
            # ─ 발주처: DB 목록에서 선택 또는 직접 입력
            {
                "type": "input",
                "block_id": "block_new_task_client",
                "optional": True,
                "label": {"type": "plain_text", "text": "* 발주처 (목록 선택)"},
                "hint": {"type": "plain_text", "text": "목록에 없으면 아래 '직접 입력'란을 사용하세요."},
                "element": {
                    "type": "static_select",
                    "action_id": "new_task_client",
                    "placeholder": {"type": "plain_text", "text": "발주처 선택"},
                    "options": client_options,
                }
            },
            {
                "type": "input",
                "block_id": "block_new_task_client_text",
                "optional": True,
                "label": {"type": "plain_text", "text": "※ 신규 발주처 직접입력"},
                "hint": {"type": "plain_text", "text": "입력 시 위 선택보다 우선 적용됩니다. 한글 검색어 그대로 입력하세요."},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "new_task_client_text",
                    "placeholder": {"type": "plain_text", "text": "예: 청주시청, 한국농어촌공사"},
                }
            },
            # ─ 소분류
            {
                "type": "input",
                "block_id": "block_new_task_sub",
                "label": {"type": "plain_text", "text": "② 소분류 (읍면동·사업유형) *"},
                "hint": {"type": "plain_text", "text": "예: 도시재생, 덕산면, 전략계획, 경영지원"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "new_task_sub",
                    "placeholder": {"type": "plain_text", "text": "도시재생"},
                }
            },
            # ─ 결과물명
            {
                "type": "input",
                "block_id": "block_new_task_name",
                "label": {"type": "plain_text", "text": "③ 결과물명 *"},
                "hint": {"type": "plain_text", "text": "15자 이내 명사형"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "new_task_name",
                    "placeholder": {"type": "plain_text", "text": "컨설팅 일정 확정"},
                }
            },
            # ─ 마감일
            {
                "type": "input",
                "block_id": "block_new_task_deadline",
                "optional": True,
                "label": {"type": "plain_text", "text": "마감일 (선택)"},
                "element": {
                    "type": "datepicker",
                    "action_id": "new_task_deadline",
                    "placeholder": {"type": "plain_text", "text": "마감일 선택"},
                }
            },
            # ─ 현재단계
            {
                "type": "input",
                "block_id": "block_new_task_phase",
                "optional": True,
                "label": {"type": "plain_text", "text": "현재단계 (선택)"},
                "element": {
                    "type": "static_select",
                    "action_id": "new_task_phase",
                    "placeholder": {"type": "plain_text", "text": "단계 선택"},
                    "options": phase_options,
                }
            },
            # ─ 진행상황
            {
                "type": "input",
                "block_id": "block_new_task_status",
                "optional": True,
                "label": {"type": "plain_text", "text": "진행상황"},
                "element": {
                    "type": "static_select",
                    "action_id": "new_task_status",
                    "placeholder": {"type": "plain_text", "text": "진행 예정"},
                    "options": new_task_status_options,
                    "initial_option": {"text": {"type": "plain_text", "text": "🙏 진행 예정"}, "value": "🙏 진행 예정"},
                }
            },
        ]


    # 담당자 선택 블록: 새 Task 생성 시에만 정의
    assignee_block = []
    if is_new:
        assignee_block = [
            {
                "type": "input",
                "block_id": "block_assignee",
                "label": {"type": "plain_text", "text": "👤 담당자 지정"},
                "hint": {"type": "plain_text", "text": "본인 또는 해당 업무의 담당자를 선택해 주세요."},
                "element": {
                    "type": "users_select",
                    "action_id": "assignee_select",
                    "initial_user": user_id if user_id else None,
                    "placeholder": {"type": "plain_text", "text": "담당자 선택"},
                }
            }
        ]

    header_text = f"*{task_name}* 업무의 일지를 작성합니다."
    if not is_new:
        header_text += f" ({step}/{total})"

    has_todos = bool(todos and not is_new)

    task_info_block = [{
        "type": "section",
        "text": {"type": "mrkdwn", "text": header_text}
    }]

    # 진행 상황 선택 (기존 Task인 경우에만 표시)
    status_block = []
    if not is_new:
        status_options = [
            {"text": {"type": "plain_text", "text": s}, "value": s}
            for s in STATUS_OPTIONS
        ]
        initial_opt = None
        if current_status:
            for opt in status_options:
                if opt["value"] == current_status:
                    initial_opt = opt
                    break

        status_block = [
            {
                "type": "input",
                "block_id": "block_status",
                "label": {"type": "plain_text", "text": "🏃 진행 상황 변경"},
                "optional": False,
                "element": {
                    "type": "static_select",
                    "action_id": "status_select",
                    "placeholder": {"type": "plain_text", "text": "상태 변경 시 선택"},
                    "options": status_options,
                    "initial_option": initial_opt if initial_opt else None,
                },
            }
        ]

    # title은 25자 제한이므로 간결하게
    title_text = f"📝 일지 ({step}/{total})"

    submit_text = "제출" if step == total else "다음 →"

    return {
        "type": "modal",
        "callback_id": "modal_log_submit",
        "private_metadata": metadata_json,
        "title": {"type": "plain_text", "text": title_text},
        "submit": {"type": "plain_text", "text": submit_text},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": [
            *task_info_block,
            *status_block,
            *assignee_block, # is_new=True일 때만 데이터가 있음
            *new_task_blocks,
            {"type": "divider"},
            *([
                {
                    "type": "input",
                    "block_id": "block_todo_check",
                    "optional": True,
                    "label": {"type": "plain_text", "text": "📋 To-do 진행 현황"},
                    "hint": {"type": "plain_text", "text": "이번에 '새롭게' 완료한 항목만 체크하세요. ('오늘 완료'에 자동 기록)"},
                    "element": {
                        "type": "checkboxes",
                        "action_id": "todo_checkboxes",
                        "options": [
                            {"text": {"type": "plain_text", "text": t["text"][:74] if len(t["text"]) <= 74 else t["text"][:71]+"..."}, "value": t["id"]}
                            for t in todos
                        ],
                        **(({"initial_options": [
                            {"text": {"type": "plain_text", "text": t["text"][:74] if len(t["text"]) <= 74 else t["text"][:71]+"..."}, "value": t["id"]}
                            for t in todos if t.get("checked")
                        ]}) if any(t.get("checked") for t in todos) else {})
                    }
                }
            ] if todos and not is_new else []),
            {
                "type": "input",
                "block_id": f"block_log_date_{step}",
                "label": {"type": "plain_text", "text": "📅 일지 날짜"},
                "element": {
                    "type": "datepicker",
                    "action_id": f"log_date_{step}",
                    "initial_date": datetime.date.today().isoformat(),
                    "placeholder": {"type": "plain_text", "text": "날짜 선택"},
                }
            },
            {
                "type": "input",
                "block_id": f"block_daily_log_{step}",
                "optional": True,
                "label": {"type": "plain_text", "text": "📝 데일리 로그"},
                "hint": {"type": "plain_text",
                         "text": "오늘 한 일, 메모 등 자유롭게 적어주세요. To-do에는 반영되지 않습니다."},
                "element": {
                    "type": "plain_text_input",
                    "action_id": f"daily_log_{step}",
                    "multiline": True,
                    "placeholder": {"type": "plain_text",
                                    "text": "오늘 작업 내용, 특이사항 등을 자유롭게 입력하세요"},
                }
            },
            {
                "type": "input",
                "block_id": f"block_todo_add_{step}",
                "optional": True,
                "label": {"type": "plain_text", "text": "📌 To-do 추가"},
                "hint": {"type": "plain_text",
                         "text": "새로 추가할 업무 항목을 적어주세요. 노션 Task의 To-do 체크박스로 추가됩니다."},
                "element": {
                    "type": "plain_text_input",
                    "action_id": f"todo_add_{step}",
                    "multiline": True,
                    "placeholder": {"type": "plain_text",
                                    "text": "추가할 업무를 입력하세요 (줄바꿈으로 여러 항목 입력 가능)"},
                }
            },

            {
                "type": "input",
                "block_id": f"block_consultation_{step}",
                "optional": True,
                "label": {"type": "plain_text", "text": "🤝 협의/보고"},
                "hint": {"type": "plain_text",
                         "text": "발주처·기관과 협의하거나 보고한 내용이 있으면 적어주세요."},
                "element": {
                    "type": "plain_text_input",
                    "action_id": f"consultation_{step}",
                    "multiline": True,
                    "placeholder": {"type": "plain_text",
                                    "text": "협의 또는 보고 내용을 입력하세요"},
                }
            },
            {
                "type": "input",
                "block_id": f"block_issues_{step}",
                "optional": True,
                "label": {"type": "plain_text", "text": "⚠️ 이슈/결정사항"},
                "hint": {"type": "plain_text",
                         "text": "팀이 알아야 할 문제나 중요한 합의 내용을 적어주세요."},
                "element": {
                    "type": "plain_text_input",
                    "action_id": f"issues_{step}",
                    "multiline": True,
                    "placeholder": {"type": "plain_text",
                                    "text": "이슈 또는 결정사항이 있으면 입력하세요"},
                }
            },
            {
                "type": "input",
                "block_id": f"block_risk_{step}",
                "optional": True,
                "label": {"type": "plain_text", "text": "🚨 마감 리스크"},
                "hint": {"type": "plain_text",
                         "text": "납품 D-7 이내이거나 일정 지연 우려가 있으면 적어주세요."},
                "element": {
                    "type": "plain_text_input",
                    "action_id": f"risk_{step}",
                    "placeholder": {"type": "plain_text",
                                    "text": "마감 관련 리스크가 있으면 입력하세요"},
                }
            },
        ]
    }


# ════════════════════════════════════════════════════════════
# 3. 완료/오류 메시지
# ════════════════════════════════════════════════════════════

def build_success_message(task_name: str, task_url: str,
                          is_new: bool = False) -> list:
    action_text = "✅ 새 Task가 생성되고 일지가 기록" if is_new else "✅ 일지가 기록"
    link_part = f"\n<{task_url}|📎 노션에서 확인하기>" if task_url else ""
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"{action_text}됐습니다!\n*{task_name}*{link_part}"}
        }
    ]


def build_multi_success_message(done: list[dict]) -> list:
    """복수 Task 일지 완료 메시지.
    done: [{"name": str, "url": str, "is_new": bool}, ...]
    """
    lines = []
    for item in done:
        prefix = "✨ " if item.get("is_new") else ""
        suffix = " (새 Task)" if item.get("is_new") else ""
        if item.get("url"):
            lines.append(f"• {prefix}{item['name']}{suffix} — <{item['url']}|📎 노션에서 확인>")
        else:
            lines.append(f"• {prefix}{item['name']}{suffix}")

    text = f"✅ 일지가 기록됐습니다! ({len(done)}건)\n" + "\n".join(lines)
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text}
        }
    ], text


def build_daily_reminder_message() -> list:
    """매일 17시 알림 메시지 블록 (일지 작성 버튼 포함)."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "🕐 오늘의 업무일지를 작성해 주세요.",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📝 일지 작성"},
                    "action_id": "open_ilji_modal",
                    "style": "primary",
                }
            ],
        },
    ]


def build_error_message(message: str) -> list:
    return [{
        "type": "section",
        "text": {"type": "mrkdwn",
                 "text": f"❌ 오류가 발생했습니다.\n{message}\n\n잠시 후 다시 시도하거나 관리자에게 문의하세요."}
    }]


# ════════════════════════════════════════════════════════════
# 4. 주간 요약 메시지
# ════════════════════════════════════════════════════════════

def _group_by_log_author(logs: list[dict]) -> dict[str, list]:
    """로그 작성자(실명)별로 데이터를 그룹화합니다."""
    grouped: dict[str, list] = {}
    for log in logs:
        author = log.get("author", "알 수 없음")
        grouped.setdefault(author, []).append(log)
    return grouped


def _build_log_line(log: dict) -> str:
    """주간 요약용 개별 로그 라인 구성."""
    task_name = log.get("task_name", "(제목 없음)")
    task_url = log.get("task_url", "")
    client = log.get("client", "")
    status = log.get("status", "")
    
    name_link = f"<{task_url}|{task_name}>" if task_url else task_name
    client_str = f"*{client}* | " if client else ""
    status_str = f" ({status})" if status else ""
    
    lines = [f"• {client_str}{name_link}{status_str}"]
    
    # 과정 기록(오늘 완료) 추가
    if log.get("completed"):
        # 불릿 포인트가 너무 많으면 요약
        comp = log["completed"].strip()
        if len(comp) > 150: comp = comp[:147] + "..."
        lines.append(f"> {comp}")
        
    return "\n".join(lines)


def build_weekly_summary_message(logs: list[dict]) -> list:
    """
    작성자별로 그룹화된 주간 요약 블록 빌더.
    tasks 대신 logs 데이터를 직접 사용합니다.
    """
    if not logs:
        return [{
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "📊 *주간 요약*\n\n이번 주 기록된 업무일지가 없습니다."},
        }]

    grouped = _group_by_log_author(logs)
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📊 주간 요약 (인원별 수행 업무)"},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn",
                          "text": f"이번 주 총 {len(logs)}건의 일지가 기록되었습니다."}],
        },
        {"type": "divider"},
    ]

    for author, author_logs in grouped.items():
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*👤 {author}* ({len(author_logs)}건)"},
        })

        # 동일한 업무에 여러 로그가 있을 수 있으므로 태스크별로 한 번 더 묶어주면 좋음
        # 여기서는 단순 나열하되, 가독성을 위해 간결하게 처리
        for log in author_logs:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": _build_log_line(log)},
            })

        blocks.append({"type": "divider"})

        if len(blocks) >= 48:
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn",
                              "text": "⚠️ 내용이 너무 길어 일부를 생략했습니다."}],
            })
            break

    return blocks


# ════════════════════════════════════════════════════════════
# 5. 인수인계 모달 + 메시지
# ════════════════════════════════════════════════════════════

def build_handover_select_modal(tasks: list[dict]) -> dict:
    """인수인계 대상 Task를 static_select 드롭다운으로 1개 선택."""
    options = []
    for task in tasks[:100]:
        label = _task_label(task)
        options.append({
            "text": {"type": "plain_text", "text": label},
            "value": task["id"],
        })

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "인수인계 초안을 생성할 Task를 선택하세요."},
        },
        {
            "type": "input",
            "block_id": "block_handover_task",
            "label": {"type": "plain_text", "text": "Task 선택"},
            "element": {
                "type": "static_select",
                "action_id": "handover_task_select",
                "placeholder": {"type": "plain_text", "text": "Task를 선택하세요"},
                "options": options,
            },
        },
    ]

    return {
        "type": "modal",
        "callback_id": "modal_handover_select",
        "title": {"type": "plain_text", "text": "📋 인수인계 초안"},
        "submit": {"type": "plain_text", "text": "생성"},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": blocks,
    }


def build_handover_message(task: dict, logs: list[dict]) -> list:
    """
    인수인계 초안 Slack 메시지 블록.
    task: _parse_task() 결과, logs: get_handover_data() 결과.
    """
    # Task 기본 정보
    info_parts = [f"*📋 인수인계 초안 — {task['name']}*"]
    if task.get("client"):
        info_parts.append(f"• 발주처: {task['client']}")
    if task.get("deadline"):
        info_parts.append(f"• 마감일: {task['deadline']}")
    if task.get("phase"):
        info_parts.append(f"• 현재단계: {task['phase']}")
    if task.get("assignees"):
        info_parts.append(f"• 담당자: {', '.join(task['assignees'])}")
    if task.get("url"):
        info_parts.append(f"<{task['url']}|📎 노션에서 확인하기>")

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(info_parts)},
        },
        {"type": "divider"},
    ]

    if not logs:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "기록된 이슈/리스크가 없습니다."},
        })
        return blocks

    # 날짜별 이슈/리스크
    for log in logs:
        lines = [f"*📅 {log['date']}* ({log['author']})"]
        if log.get("issues"):
            lines.append(f"⚠️ 이슈/결정사항: {log['issues']}")
        if log.get("risk"):
            lines.append(f"🚨 마감 리스크: {log['risk']}")

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(lines)},
        })

        if len(blocks) >= 48:
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn",
                              "text": "⚠️ 내용이 너무 길어 일부를 생략했습니다."}],
            })
            break

    return blocks


def build_kpi_report_message(tasks: list[dict]) -> list:
    """
    대표 전용 KPI 리포트.
    개인별: 담당 Task 수, 일지 작성 건수, 마감 임박 건수.
    """
    import datetime

    if not tasks:
        return [{
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "📈 *KPI 리포트*\n\n이번 주 업데이트된 Task가 없습니다."},
        }]

    grouped = _group_by_person(tasks)
    today = datetime.date.today()

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📈 주간 KPI 리포트"},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn",
                          "text": f"이번 주 업데이트된 Task {len(tasks)}건 · 이 메시지는 본인에게만 표시됩니다"}],
        },
        {"type": "divider"},
    ]

    for person, person_tasks in grouped.items():
        log_count = sum(len(t.get("weekly_logs", [])) for t in person_tasks)
        risk_count = 0
        for t in person_tasks:
            if t.get("deadline"):
                try:
                    dl = datetime.date.fromisoformat(t["deadline"])
                    if dl <= today + datetime.timedelta(days=7):
                        risk_count += 1
                except ValueError:
                    pass

        risk_str = f" · 🚨 마감임박 {risk_count}건" if risk_count else ""
        kpi_line = f"Task {len(person_tasks)}건 · 일지 {log_count}건{risk_str}"

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*👤 {person}*\n{kpi_line}"},
        })

        for task in person_tasks:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": _build_task_line(task, today)},
            })

        blocks.append({"type": "divider"})

        if len(blocks) >= 48:
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn",
                              "text": "⚠️ 요약이 너무 길어 일부를 생략했습니다."}],
            })
            break

    return blocks


def build_deadline_risk_message(tasks: list[dict]) -> list:
    """마감리스크 항목 전용 알림 메시지 빌더."""
    if not tasks:
        return []

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🚨 마감리스크 업무 보고", "emoji": True}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "현재 노션 Task DB에서 *마감리스크*가 감지된 업무 목록입니다."}
        },
        {"type": "divider"}
    ]

    for t in tasks:
        notion_url = t.get("url", "")
        name_link = f"<{notion_url}|*{t['name']}*>" if notion_url else f"*{t['name']}*"

        # 주요 정보 구성 (라벨 추가)
        info_parts = []
        if t.get("client"):   info_parts.append(f"🏢 *발주처:* {t['client']}")
        if t.get("deadline"): info_parts.append(f"📅 *마감일:* ~{t['deadline']}")
        
        assignees = ", ".join(t.get("assignees", []))
        if assignees: info_parts.append(f"👤 *담당자:* {assignees}")

        info_text = "\n".join(info_parts)

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"📌 {name_link}\n{info_text}"}
        })

        # 리스크 내용 강조
        risk_content = t.get("risk_content", "").strip()
        if risk_content:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"> ⚠️ *리스크 세부내용*\n> {risk_content}"}
            })
        else:
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "ℹ️ *최근 입력된 리스크 상세 내용이 없습니다.*"}]
            })
        
        blocks.append({"type": "divider"})

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "위 리스크 업무의 원활한 마감을 위해 팀 내 긴밀한 협의를 부탁드립니다."}]
    })

    return blocks
