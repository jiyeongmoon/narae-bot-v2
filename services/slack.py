"""
services/slack.py — 슬랙 모달·메시지 블록 빌더
=================================================
"""

import datetime
import json
import re

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

    # ── 내 업무 섹션 (Slack checkboxes 최대 10개 제한) ─────────
    MAX_CHECKBOX_OPTIONS = 10
    if my_tasks:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": self_header},
        })
        displayed_my = my_tasks[:MAX_CHECKBOX_OPTIONS]
        remaining_my = len(my_tasks) - len(displayed_my)
        blocks.append({
            "type": "input",
            "block_id": "block_my_tasks",
            "optional": True,
            "label": {"type": "plain_text", "text": f"내 업무 (최신 {len(displayed_my)}건)"},
            "element": {
                "type": "checkboxes",
                "action_id": "my_task_checkboxes",
                "options": [_make_option(t) for t in displayed_my],
            }
        })
        if remaining_my > 0:
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn",
                              "text": f"_외 {remaining_my}건 — 🔍 키워드 검색으로 찾을 수 있습니다._"}],
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
    # ★ 블록 상한 가드(2026-07-28): 담당자가 많으면 Slack 모달 100블록 한도를 초과해
    #   views_open/update가 실패(버튼 ⚠️)하던 문제 → 한도 근처에서 멈추고 검색으로 유도.
    _MODAL_BLOCK_CAP = 96   # 100 - 트레일링 안내/여유
    if other_groups:
        _omitted_people = 0
        for person, person_tasks in other_groups.items():
            opts = [_make_option(t) for t in person_tasks[:5]]
            if not opts:
                continue
            if len(blocks) + 3 > _MODAL_BLOCK_CAP:   # 이 그룹(divider+section+input=3) 넣으면 초과
                _omitted_people += 1
                continue
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
                    "options": opts,
                }
            })
        if _omitted_people:
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn",
                              "text": f"_외 담당자 {_omitted_people}명 업무는 🔍 키워드 검색으로 확인하세요._"}],
            })

    if search_keyword and not (my_tasks or unassigned_tasks or other_groups):
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "검색 결과가 없습니다."},
        })

    # (새 Task 블록은 이미 위에서 검색 블록 바로 뒤에 추가됨)

    # ★ 안전망: Slack 모달 블록 한도는 100. 어떤 경로로든 넘으면 잘라 실패를 방지.
    blocks = blocks[:100]

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
                    "label": {"type": "plain_text", "text": f"📋 To-do 진행 현황 ({len(todos)}건 중 최대 10건 표시)" if len(todos) > 10 else "📋 To-do 진행 현황"},
                    "hint": {"type": "plain_text", "text": "이번에 '새롭게' 완료한 항목만 체크하세요. ('오늘 완료'에 자동 기록)" + (f" — 미표시 {len(todos) - 10}건은 노션에서 직접 관리해 주세요." if len(todos) > 10 else "")},
                    "element": {
                        "type": "checkboxes",
                        "action_id": "todo_checkboxes",
                        "options": [
                            {"text": {"type": "plain_text", "text": t["text"][:74] if len(t["text"]) <= 74 else t["text"][:71]+"..."}, "value": t["id"]}
                            for t in (sorted(todos, key=lambda x: (x.get("checked", False),))[:10])
                        ],
                        **(({"initial_options": [
                            {"text": {"type": "plain_text", "text": t["text"][:74] if len(t["text"]) <= 74 else t["text"][:71]+"..."}, "value": t["id"]}
                            for t in (sorted(todos, key=lambda x: (x.get("checked", False),))[:10]) if t.get("checked")
                        ]}) if any(t.get("checked") for t in (sorted(todos, key=lambda x: (x.get("checked", False),))[:10])) else {})
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
# 4. 인수인계 모달 + 메시지
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


# ════════════════════════════════════════════════════════════
# 5. 회의록 Task 검토 모달 + 알림 메시지
# ════════════════════════════════════════════════════════════

PRIORITY_OPTIONS_DISPLAY = ["P1 (긴급)", "P2 (보통)", "P3 (낮음)", "없음"]

# "P1 (긴급)" → "P1" 등 역매핑 (modal submit 시 사용)
PRIORITY_DISPLAY_TO_CODE = {
    "P1 (긴급)": "P1",
    "P2 (보통)": "P2",
    "P3 (낮음)": "P3",
    "없음": "",
}

# AI 출력 코드 "P1" → 표시 라벨 "P1 (긴급)"
PRIORITY_CODE_TO_DISPLAY = {
    "P1": "P1 (긴급)",
    "P2": "P2 (보통)",
    "P3": "P3 (낮음)",
}


def build_meeting_notification(session_id: str, filename: str, task_count: int) -> list:
    """회의록 Task 검토 알림 메시지 블록."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"📋 *회의록 처리 완료 — Task 검토 요청*\n"
                    f"파일: `{filename}`\n"
                    f"추출된 Task: *{task_count}건*\n"
                    f"담당자·우선순위·마감일을 확인 후 Notion에 등록해 주세요."
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🔍 Task 검토하기"},
                    "action_id": "open_meeting_review_modal",
                    "value": session_id,
                    "style": "primary",
                }
            ],
        },
    ]


def build_meeting_only_notification(filename: str, meeting_title: str = "",
                                    meeting_page_url: str = "") -> list:
    """추출된 Task가 없을 때(회의록만 등록) 보내는 알림 블록. 검토 버튼 없음."""
    if meeting_page_url:
        link = f"<{meeting_page_url}|{meeting_title or '회의록 보기'}>"
    else:
        link = meeting_title or "(링크 없음)"
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"📋 *회의록 등록 완료 — 추출된 Task 없음*\n"
                    f"파일: `{filename}`\n"
                    f"회의록: {link}"
                ),
            },
        },
    ]


def build_meeting_review_modal(
    session_id: str,
    tasks: list[dict],
    managers: list[dict],
    filename: str = "",
    channel_id: str = "",
    loose_todos: list = None,
    active_tasks: list = None,
    notif_ts: str = "",
) -> dict:
    """
    회의록 Task 검토 모달.

    tasks  : 각 task — 업무명, 담당자(hint), 우선순위(hint), 마감일(hint), 발주처, 내용 포함
    managers : [{"name": str, "notion_id": str}, ...]
    """
    # ── 담당자 옵션 빌드 ──────────────────────────────────────────
    manager_options = [
        {"text": {"type": "plain_text", "text": m["name"]}, "value": m["notion_id"]}
        for m in managers
        if m.get("notion_id")
    ]
    mgr_opts_with_none = [
        {"text": {"type": "plain_text", "text": "— 미지정 —"}, "value": "__none__"},
        *manager_options,
    ]

    # ── 우선순위 옵션 빌드 ────────────────────────────────────────
    priority_opts = [
        {"text": {"type": "plain_text", "text": p}, "value": p}
        for p in PRIORITY_OPTIONS_DISPLAY
    ]

    # ── 헤더 블록 ─────────────────────────────────────────────────
    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*📋 회의록 Task 검토* — `{filename}`\n"
                    f"총 *{len(tasks)}건*. 담당자·우선순위·마감일을 확인 후 제출하세요.\n"
                    f"_제외할 Task는 '이 Task 건너뛰기'를 체크하세요._"
                ),
            },
        },
        {"type": "divider"},
    ]

    # ── Task 별 블록 ─────────────────────────────────────────────
    for i, task in enumerate(tasks):
        task_name     = task.get("업무명", f"Task {i + 1}")
        assignee_hint = task.get("담당자", "")
        priority_hint = task.get("우선순위", "")
        deadline_hint = task.get("마감일", "")
        client_hint   = task.get("발주처", "")

        hint_parts = []
        if assignee_hint: hint_parts.append(f"담당자: {assignee_hint}")
        if priority_hint: hint_parts.append(f"우선순위: {priority_hint}")
        if deadline_hint: hint_parts.append(f"마감: {deadline_hint}")
        if client_hint:   hint_parts.append(f"발주처: {client_hint}")
        hint_text = " / ".join(hint_parts) if hint_parts else "AI 힌트 없음"

        # 제목 + 힌트
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*[{i + 1}] {task_name}*"},
        })
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"💡 AI 제안: {hint_text}"}],
        })

        # ── ✅ 이 Task의 To-do 확인·선택 (체크 해제 = 이 Task에서 제외) ──
        _td_lines = [l.strip() for l in (task.get("내용", "") or "").splitlines()
                     if re.match(r"^-\s*\[[ xX]?\]", l.strip())]
        if _td_lines:
            _td_opts = []
            for _ti, _l in enumerate(_td_lines[:10]):        # Slack checkboxes 최대 10
                _lab = re.sub(r"^-\s*\[[ xX]?\]\s*", "", _l).strip() or _l
                _td_opts.append({"text": {"type": "plain_text", "text": _lab[:74]},
                                 "value": str(_ti)})
            blocks.append({
                "type": "input", "block_id": f"task_{i}_todos", "optional": True,
                "label": {"type": "plain_text", "text": "✅ To-do (유지할 항목)"},
                "hint": {"type": "plain_text", "text": "체크 해제한 항목은 이 Task에 넣지 않습니다."},
                "element": {"type": "checkboxes", "action_id": "todo_checks",
                            "options": _td_opts, "initial_options": _td_opts},
            })
            if len(_td_lines) > 10:
                blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
                    "text": f"_To-do {len(_td_lines)}개 중 앞 10개만 선택 가능 — 나머지는 그대로 유지._"}]})

        # 담당자 선택 — 힌트 이름으로 initial_option 자동 매칭
        initial_assignee: dict | None = None
        if assignee_hint:
            for opt in manager_options:
                opt_name = opt["text"]["text"]
                if assignee_hint in opt_name or opt_name in assignee_hint:
                    initial_assignee = opt
                    break

        blocks.append({
            "type": "input",
            "block_id": f"task_{i}_assignee",
            "optional": True,
            "label": {"type": "plain_text", "text": "👤 담당자"},
            "element": {
                "type": "static_select",
                "action_id": "assignee_select",
                "placeholder": {"type": "plain_text", "text": "담당자 선택"},
                "options": mgr_opts_with_none,
                **({"initial_option": initial_assignee} if initial_assignee else {}),
            },
        })

        # 우선순위 — 힌트 코드(P1/P2/P3)로 initial_option 자동 매칭
        initial_priority: dict | None = None
        priority_label = PRIORITY_CODE_TO_DISPLAY.get(priority_hint, "")
        if priority_label:
            for opt in priority_opts:
                if opt["value"] == priority_label:
                    initial_priority = opt
                    break

        blocks.append({
            "type": "input",
            "block_id": f"task_{i}_priority",
            "optional": True,
            "label": {"type": "plain_text", "text": "🎯 우선순위"},
            "element": {
                "type": "static_select",
                "action_id": "priority_select",
                "placeholder": {"type": "plain_text", "text": "우선순위 선택"},
                "options": priority_opts,
                **({"initial_option": initial_priority} if initial_priority else {}),
            },
        })

        # 마감일 — 유효한 YYYY-MM-DD 형식이면 initial_date 설정
        deadline_elem: dict = {
            "type": "datepicker",
            "action_id": "deadline_pick",
            "placeholder": {"type": "plain_text", "text": "날짜 선택"},
        }
        if deadline_hint and re.match(r"^\d{4}-\d{2}-\d{2}$", deadline_hint):
            deadline_elem["initial_date"] = deadline_hint

        blocks.append({
            "type": "input",
            "block_id": f"task_{i}_deadline",
            "optional": True,
            "label": {"type": "plain_text", "text": "📅 마감일"},
            "element": deadline_elem,
        })

        # 제외 체크박스
        blocks.append({
            "type": "input",
            "block_id": f"task_{i}_exclude",
            "optional": True,
            "label": {"type": "plain_text", "text": "🚫 제외"},
            "element": {
                "type": "checkboxes",
                "action_id": "exclude_check",
                "options": [
                    {
                        "text": {"type": "plain_text", "text": "이 Task 건너뛰기"},
                        "value": "exclude",
                    }
                ],
            },
        })

        # 🔗 병합: 같은 산출물의 기존 Notion Task 후보가 있으면 체크박스(기본 체크)
        cand = task.get("_merge_candidate")
        if cand:
            merge_opt = {
                "text": {"type": "plain_text", "text": f"기존 『{cand['name'][:55]}』에 병합"},
                "value": cand["id"],
            }
            blocks.append({
                "type": "input",
                "block_id": f"task_{i}_merge",
                "optional": True,
                "label": {"type": "plain_text", "text": "🔗 병합"},
                "element": {
                    "type": "checkboxes",
                    "action_id": "merge_check",
                    "options": [merge_opt],
                    "initial_options": [merge_opt],
                },
            })
        elif task.get("_pending_notice"):
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"⚠️ {task['_pending_notice']}"}],
            })

        blocks.append({"type": "divider"})

        # Slack 모달 블록 상한(100) 초과 방지
        if len(blocks) >= 96:
            blocks.append({
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": f"⚠️ Task가 많아 {i + 1}번까지만 표시됩니다. 나머지는 Notion에서 직접 확인하세요.",
                }],
            })
            break

    # ── ✅ To-do (산출물 없는 단독 항목 — 어느 Task에 넣을지 배정) ──────
    loose_todos = loose_todos or []
    active_tasks = active_tasks or []
    if loose_todos and len(blocks) < 88:
        blocks.append({"type": "divider"})
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": ("*✅ To-do (산출물 없는 항목)*\n"
                     "_각 항목을 어느 Task에 넣을지 선택하세요. 미배정은 알림으로만 전달됩니다._")}})
        route_opts = [
            {"text": {"type": "plain_text", "text": "미배정 (알림만)"}, "value": "none"},
            {"text": {"type": "plain_text", "text": "🗑 삭제 (완전 제외)"}, "value": "delete"},
        ]
        for ti, t in enumerate(tasks):
            nm = (t.get("업무명", f"Task {ti + 1}") or "")[:50]
            route_opts.append({"text": {"type": "plain_text", "text": f"➕ 새[{ti + 1}] {nm}"},
                               "value": f"new:{ti}"})
        for at in active_tasks:
            route_opts.append({"text": {"type": "plain_text", "text": f"📌 기존 {(at.get('name') or '')[:55]}"},
                               "value": f"exist:{at.get('id')}"})
        route_opts = route_opts[:100]
        for j, todo in enumerate(loose_todos):
            if len(blocks) >= 96:
                break
            blocks.append({"type": "section",
                           "text": {"type": "mrkdwn", "text": f"• {str(todo)[:140]}"}})
            blocks.append({
                "type": "input", "block_id": f"todo_{j}", "optional": True,
                "label": {"type": "plain_text", "text": "처리"},
                "element": {
                    "type": "static_select", "action_id": "todo_route",
                    "options": route_opts,
                    "initial_option": route_opts[0],
                },
            })

    metadata = json.dumps(
        {"session_id": session_id, "channel_id": channel_id, "notif_ts": notif_ts},
        ensure_ascii=False,
    )

    return {
        "type": "modal",
        "callback_id": "modal_meeting_review",
        "private_metadata": metadata,
        "title": {"type": "plain_text", "text": "📋 회의 Task 검토"},
        "submit": {"type": "plain_text", "text": "Notion에 등록"},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": blocks,
    }
