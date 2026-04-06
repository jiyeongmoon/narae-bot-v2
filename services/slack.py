"""
services/slack.py — 슬랙 모달·메시지 블록 빌더
=================================================
"""

import datetime
import json

from services.notion import CLIENT_OPTIONS, PHASE_OPTIONS


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


def build_task_select_modal(tasks: list[dict],
                            user_real_name: str = "",
                            search_keyword: str = "",
                            filter_user_id: str = None,
                            filter_user_name: str = "") -> dict:
    """활성 Task를 [담당자별] 섹션으로 분류하여 표시."""

    # ── 필터링: 진행 중(🚀) 및 진행 예정(🙏) 상태만 ────────────────
    ACTIVE_STATUSES = ["🚀 진행 중", "🙏 진행 예정"]
    filtered_tasks = [t for t in tasks if t.get("status") in ACTIVE_STATUSES]

    # 담당자별 그룹화 (services/slack.py의 _group_by_person 활용)
    grouped = _group_by_person(filtered_tasks)

    # 본인 업무 (기존 is_assigned 활용)
    my_tasks = [t for t in filtered_tasks if t.get("is_assigned")]
    
    # 미배정 업무
    unassigned_tasks = grouped.get("미배정", [])

    # 타인 업무 (본인 및 미배정 제외)
    # _group_by_person 결과에서 본인은 slack_display_name(또는 is_assigned)으로 걸러야 함
    # 여기서는 간단히 is_assigned가 False인 그룹들만 추출
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
        # ── 새 Task 생성 (최상단으로 이동) ───────────────────────
        {"type": "divider"},
        {
            "type": "input",
            "block_id": "block_new_task_select",
            "optional": True,
            "label": {"type": "plain_text", "text": "새 Task (선택 사항)"},
            "element": {
                "type": "checkboxes",
                "action_id": "new_task_checkboxes",
                "options": [
                    {"text": {"type": "plain_text", "text": "➕ 새 Task 생성"}, "value": "NEW_TASK"}
                ],
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
        client_options = [
            {"text": {"type": "plain_text", "text": c}, "value": c}
            for c in CLIENT_OPTIONS
        ]
        phase_options = [
            {"text": {"type": "plain_text", "text": p}, "value": p}
            for p in PHASE_OPTIONS
        ]
        PREFIX_OPTIONS = [
            {"text": {"type": "plain_text", "text": "진천"}, "value": "진천"},
            {"text": {"type": "plain_text", "text": "무주"}, "value": "무주"},
            {"text": {"type": "plain_text", "text": "청주"}, "value": "청주"},
            {"text": {"type": "plain_text", "text": "괴산"}, "value": "괴산"},
            {"text": {"type": "plain_text", "text": "음성"}, "value": "음성"},
            {"text": {"type": "plain_text", "text": "내부"}, "value": "내부"},
            {"text": {"type": "plain_text", "text": "기타"}, "value": "기타"},
        ]
        new_task_status_options = [
            {"text": {"type": "plain_text", "text": s}, "value": s}
            for s in STATUS_OPTIONS
        ]
        new_task_blocks = [
            # ─ 업무명 안내 텍스트
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "📌 *업무명 규칙*: `[대분류_소분류]` + 결과물명\n예) `[진천_도시재생] 컨설팅 일정 확정`  `[내부_경영지원] 2월 지출결의서 취합`"},
            },
            # ─ 대분류 (발주처)
            {
                "type": "input",
                "block_id": "block_new_task_prefix",
                "label": {"type": "plain_text", "text": "① 대분류 (발주처/조직) *"},
                "element": {
                    "type": "static_select",
                    "action_id": "new_task_prefix",
                    "placeholder": {"type": "plain_text", "text": "예: 진천, 무주, 내부"},
                    "options": PREFIX_OPTIONS,
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
                "hint": {"type": "plain_text", "text": "15자 이내 명사형으로 간결하게 작성"},
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
            # ─ 발주처 (노션 필드용)
            {
                "type": "input",
                "block_id": "block_new_task_client",
                "optional": True,
                "label": {"type": "plain_text", "text": "발주처 (선택)"},
                "element": {
                    "type": "static_select",
                    "action_id": "new_task_client",
                    "placeholder": {"type": "plain_text", "text": "발주처 선택"},
                    "options": client_options,
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


    # 담당자 선택 블록 추가 (항상 표시하여 명시적 배정 유도)
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
            *assignee_block,
            *new_task_blocks,
            {"type": "divider"},
            *([
                {
                    "type": "input",
                    "block_id": "block_todo_check",
                    "optional": True,
                    "label": {"type": "plain_text", "text": "📋 To-do 진행 현황 (완료 체크)"},
                    "hint": {"type": "plain_text", "text": "체크 → '오늘 완료' / 미체크 → '내일 예정' 에 자동 반영"},
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
                "block_id": "block_log_date",
                "label": {"type": "plain_text", "text": "📅 일지 날짜"},
                "element": {
                    "type": "datepicker",
                    "action_id": "log_date",
                    "initial_date": datetime.date.today().isoformat(),
                    "placeholder": {"type": "plain_text", "text": "날짜 선택"},
                }
            },
            {
                "type": "input",
                "block_id": "block_completed",
                "optional": has_todos,  # Todo 있으면 선택, 없으면 필수
                "label": {"type": "plain_text", "text": "✅ 오늘 완료"},
                "hint": {"type": "plain_text",
                         "text": "완료된 업무를 간단히 적어주세요."},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "completed",
                    "multiline": True,
                    "placeholder": {"type": "plain_text",
                                    "text": "완료한 업무 내용을 입력하세요"},
                }
            },
            {
                "type": "input",
                "block_id": "block_tomorrow",
                "optional": has_todos,  # Todo 있으면 선택, 없으면 필수
                "label": {"type": "plain_text", "text": "🔜 내일 예정"},
                "hint": {"type": "plain_text",
                         "text": "내일 진행할 업무나 이어서 할 작업을 적어주세요."},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "tomorrow",
                    "multiline": True,
                    "placeholder": {"type": "plain_text",
                                    "text": "내일 진행할 업무를 입력하세요"},
                }
            },
            {
                "type": "input",
                "block_id": "block_consultation",
                "optional": True,
                "label": {"type": "plain_text", "text": "🤝 협의/보고"},
                "hint": {"type": "plain_text",
                         "text": "발주처·기관과 협의하거나 보고한 내용이 있으면 적어주세요."},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "consultation",
                    "multiline": True,
                    "placeholder": {"type": "plain_text",
                                    "text": "협의 또는 보고 내용을 입력하세요"},
                }
            },
            {
                "type": "input",
                "block_id": "block_issues",
                "optional": True,
                "label": {"type": "plain_text", "text": "⚠️ 이슈/결정사항"},
                "hint": {"type": "plain_text",
                         "text": "팀이 알아야 할 문제나 중요한 합의 내용을 적어주세요."},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "issues",
                    "multiline": True,
                    "placeholder": {"type": "plain_text",
                                    "text": "이슈 또는 결정사항이 있으면 입력하세요"},
                }
            },
            {
                "type": "input",
                "block_id": "block_risk",
                "optional": True,
                "label": {"type": "plain_text", "text": "🚨 마감 리스크"},
                "hint": {"type": "plain_text",
                         "text": "납품 D-7 이내이거나 일정 지연 우려가 있으면 적어주세요."},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "risk",
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
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"{action_text}됐습니다!\n*{task_name}*\n<{task_url}|📎 노션에서 확인하기>"}
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
    ]


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

def _group_by_person(tasks: list[dict]) -> dict[str, list]:
    """담당자별 그룹화 (담당자 없으면 '미배정')."""
    grouped: dict[str, list] = {}
    for task in tasks:
        assignees = task.get("assignees") or []
        if not assignees:
            grouped.setdefault("미배정", []).append(task)
        else:
            for name in assignees:
                grouped.setdefault(name, []).append(task)
    return grouped


def _build_task_line(task: dict, today) -> str:
    """Task 한 줄 요약 텍스트."""
    import datetime

    risk_flag = ""
    if task.get("deadline"):
        try:
            dl = datetime.date.fromisoformat(task["deadline"])
            if dl <= today + datetime.timedelta(days=7):
                risk_flag = " 🚨"
        except ValueError:
            pass

    client_str = f"{task['client']} | " if task.get("client") else ""
    phase_str = f"{task['phase']} | " if task.get("phase") else ""
    status_str = task["status"] if task.get("status") else ""
    deadline_str = f" | ~{task['deadline']}" if task.get("deadline") else ""

    notion_url = task.get("url", "")
    name_link = f"<{notion_url}|{task['name']}>" if notion_url else task["name"]

    return f"  {name_link}\n  {client_str}{phase_str}{status_str}{deadline_str}{risk_flag}"


def build_weekly_summary_message(tasks: list[dict]) -> list:
    """
    담당자별 그룹화된 주간 요약 블록 빌더 (전체 공개용).
    50블록 초과 시 truncate.
    """
    import datetime

    if not tasks:
        return [{
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "📊 *주간 요약*\n\n이번 주 업데이트된 Task가 없습니다."},
        }]

    grouped = _group_by_person(tasks)
    today = datetime.date.today()

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📊 주간 요약"},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn",
                          "text": f"이번 주 업데이트된 Task {len(tasks)}건"}],
        },
        {"type": "divider"},
    ]

    for person, person_tasks in grouped.items():
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*👤 {person}* ({len(person_tasks)}건)"},
        })

        for task in person_tasks:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": _build_task_line(task, today)},
            })

            weekly_logs = task.get("weekly_logs", [])
            if weekly_logs:
                recent = weekly_logs[-2:]
                log_text = "\n".join(f"  • {log.split(chr(10))[0]}" for log in recent)
                if len(log_text) > 400:
                    log_text = log_text[:397] + "..."
                blocks.append({
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": log_text}],
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
