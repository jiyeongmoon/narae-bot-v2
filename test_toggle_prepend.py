"""
test_toggle_prepend.py — Notion API 토글 헤딩 + prepend 검증 스크립트
====================================================================
Phase 1 구현 전, 아래 4가지를 실제 API 호출로 검증합니다:

  1) 토글 헤딩(heading_3 + is_toggleable + children) 생성
  2) 토글 헤딩 조회 시 children 반환 여부
  3) after 파라미터로 특정 블록 뒤에 삽입
  4) position 파라미터로 맨 앞에 삽입 (notion-client 3.0.0+ 필요)

실행 방법:
  cd bot
  python test_toggle_prepend.py              # 테스트 페이지 자동 생성
  python test_toggle_prepend.py --cleanup    # 테스트 후 페이지 삭제

필요 환경변수: NOTION_TOKEN, NOTION_TASK_DB_ID (.env 자동 로드)
"""

import os
import sys
import time
import logging
from dotenv import load_dotenv
from notion_client import Client

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_TASK_DB_ID = os.environ.get("NOTION_TASK_DB_ID")

if not NOTION_TOKEN or not NOTION_TASK_DB_ID:
    log.error("❌ NOTION_TOKEN 또는 NOTION_TASK_DB_ID 환경변수가 없습니다.")
    sys.exit(1)

notion = Client(auth=NOTION_TOKEN)

# ── 헬퍼 ──────────────────────────────────────────────────────────

def paragraph(text):
    return {
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }

def toggle_heading(title, children):
    return {
        "object": "block", "type": "heading_3",
        "heading_3": {
            "rich_text": [{"type": "text", "text": {"content": title}}],
            "is_toggleable": True,
            "children": children,
        },
    }

def divider():
    return {"object": "block", "type": "divider", "divider": {}}


# ══════════════════════════════════════════════════════════════════
# 테스트 1: 토글 헤딩 생성
# ══════════════════════════════════════════════════════════════════

def test_create_toggle_heading(page_id):
    log.info("\n━━━ TEST 1: 토글 헤딩 생성 ━━━")

    blocks = [
        divider(),
        toggle_heading("📅 2026-03-01  테스트1 — ✅ 1건", [
            paragraph("✅ 완료\n첫 번째 일지 내용"),
            paragraph("🔜 내일 예정\n내일 할 일"),
        ]),
    ]

    try:
        resp = notion.blocks.children.append(block_id=page_id, children=blocks)
        created = resp.get("results", [])
        log.info(f"  ✅ 블록 {len(created)}개 생성 성공")

        # 토글 헤딩 블록 ID 반환 (test 2에서 사용)
        heading_block = None
        for b in created:
            if b["type"] == "heading_3":
                heading_block = b
                break

        if heading_block:
            is_tog = heading_block["heading_3"].get("is_toggleable", False)
            has_ch = heading_block.get("has_children", False)
            log.info(f"  → is_toggleable={is_tog}, has_children={has_ch}")
            log.info(f"  → heading block_id={heading_block['id']}")
            return heading_block["id"]
        else:
            log.warning("  ⚠️ heading_3 블록을 찾을 수 없음")
            return None

    except Exception as e:
        log.error(f"  ❌ 실패: {e}")
        return None


# ══════════════════════════════════════════════════════════════════
# 테스트 2: 토글 헤딩 조회 — children이 어떻게 반환되는지
# ══════════════════════════════════════════════════════════════════

def test_read_toggle_heading(page_id, heading_block_id):
    log.info("\n━━━ TEST 2: 토글 헤딩 조회 (children 반환 여부) ━━━")

    # 2-A: 페이지 최상위 블록 조회
    log.info("  [2-A] blocks.children.list(page_id) — 최상위 블록:")
    try:
        resp = notion.blocks.children.list(block_id=page_id)
        for b in resp.get("results", []):
            btype = b["type"]
            has_ch = b.get("has_children", False)
            if btype == "heading_3":
                rich = b["heading_3"].get("rich_text", [])
                text = rich[0]["plain_text"] if rich else ""
                # children 키가 응답에 포함되는지 확인
                children_in_resp = "children" in b.get("heading_3", {})
                log.info(f"    heading_3: '{text[:50]}' | has_children={has_ch} | children_in_response={children_in_resp}")
            else:
                log.info(f"    {btype} | has_children={has_ch}")
    except Exception as e:
        log.error(f"  ❌ 2-A 실패: {e}")

    # 2-B: 토글 헤딩의 children 직접 조회
    if heading_block_id:
        log.info(f"  [2-B] blocks.children.list(heading_block_id) — 토글 내부:")
        try:
            time.sleep(0.35)
            resp = notion.blocks.children.list(block_id=heading_block_id)
            children = resp.get("results", [])
            log.info(f"    children 수: {len(children)}")
            for c in children:
                ctype = c["type"]
                if ctype == "paragraph":
                    rich = c["paragraph"].get("rich_text", [])
                    text = "".join(r.get("plain_text", "") for r in rich)
                    log.info(f"    paragraph: '{text[:60]}'")
                else:
                    log.info(f"    {ctype}")
        except Exception as e:
            log.error(f"  ❌ 2-B 실패: {e}")


# ══════════════════════════════════════════════════════════════════
# 테스트 3: after 파라미터로 특정 블록 뒤에 삽입
# ══════════════════════════════════════════════════════════════════

def test_after_parameter(page_id):
    log.info("\n━━━ TEST 3: after 파라미터 (특정 블록 뒤 삽입) ━━━")

    # 먼저 현재 첫 번째 블록 ID 확인
    try:
        resp = notion.blocks.children.list(block_id=page_id, page_size=1)
        first_blocks = resp.get("results", [])
        if not first_blocks:
            log.warning("  ⚠️ 페이지에 블록이 없음")
            return

        first_block_id = first_blocks[0]["id"]
        log.info(f"  첫 번째 블록 ID: {first_block_id} (type={first_blocks[0]['type']})")

        # after=첫번째블록 → 첫번째블록 바로 뒤에 삽입
        time.sleep(0.35)
        new_block = paragraph("🧪 TEST3: after 파라미터로 삽입된 블록")
        resp = notion.blocks.children.append(
            block_id=page_id,
            children=[new_block],
            after=first_block_id,
        )
        created = resp.get("results", [])
        log.info(f"  ✅ after 파라미터 성공: {len(created)}개 블록 삽입")

        # 블록 순서 확인
        time.sleep(0.35)
        resp = notion.blocks.children.list(block_id=page_id)
        all_blocks = resp.get("results", [])
        log.info(f"  현재 블록 순서 ({len(all_blocks)}개):")
        for i, b in enumerate(all_blocks):
            btype = b["type"]
            if btype == "paragraph":
                rich = b["paragraph"].get("rich_text", [])
                text = "".join(r.get("plain_text", "") for r in rich)
                log.info(f"    [{i}] paragraph: '{text[:60]}'")
            elif btype == "heading_3":
                rich = b["heading_3"].get("rich_text", [])
                text = rich[0]["plain_text"] if rich else ""
                log.info(f"    [{i}] heading_3: '{text[:60]}'")
            else:
                log.info(f"    [{i}] {btype}")

    except Exception as e:
        log.error(f"  ❌ 실패: {e}")


# ══════════════════════════════════════════════════════════════════
# 테스트 4: position 파라미터로 맨 앞 삽입 (notion-client 3.0.0+ 필요)
# ══════════════════════════════════════════════════════════════════

def test_position_start(page_id):
    log.info("\n━━━ TEST 4: position 파라미터 (맨 앞 삽입, 3.0.0+ 필요) ━━━")

    # 4-A: notion-client 2.3.0에서 position이 pick()에 포함되는지 확인
    from notion_client.api_endpoints import BlocksChildrenEndpoint
    import inspect
    source = inspect.getsource(BlocksChildrenEndpoint.append)
    has_position = "position" in source
    log.info(f"  [4-A] pick()에 'position' 포함 여부: {has_position}")

    if not has_position:
        log.info("  → notion-client 2.3.0에서는 position이 pick()에서 제외됨")
        log.info("  → 3.0.0으로 업그레이드 필요")

    # 4-B: 실제 호출 시도 (2.3.0에서는 position이 무시되어 맨 뒤에 추가될 것)
    try:
        time.sleep(0.35)
        new_block = paragraph("🧪 TEST4: position=start로 삽입 시도")
        resp = notion.blocks.children.append(
            block_id=page_id,
            children=[new_block],
            position={"type": "start"},  # 2.3.0에서는 무시됨
        )
        created = resp.get("results", [])
        log.info(f"  [4-B] API 호출 자체는 성공 ({len(created)}개 블록)")

        # 실제 위치 확인
        time.sleep(0.35)
        resp = notion.blocks.children.list(block_id=page_id, page_size=10)
        all_blocks = resp.get("results", [])
        first = all_blocks[0] if all_blocks else None
        if first and first["type"] == "paragraph":
            rich = first["paragraph"].get("rich_text", [])
            text = "".join(r.get("plain_text", "") for r in rich)
            if "TEST4" in text:
                log.info("  ✅ position=start 동작 확인! (맨 앞에 삽입됨)")
                log.info("  → notion-client 2.3.0에서도 position 파라미터가 전달되고 있음!")
            else:
                log.info("  ❌ position=start 미동작 (맨 뒤에 추가됨)")
                log.info(f"  → 첫 번째 블록: {first['type']} / '{text[:50]}'")
        else:
            log.info(f"  → 첫 번째 블록: {first['type'] if first else 'None'}")

    except Exception as e:
        log.error(f"  ❌ 실패: {e}")


# ══════════════════════════════════════════════════════════════════
# 테스트 5: 기존 prepend 방식에서 토글 헤딩 children 보존 검증
# ══════════════════════════════════════════════════════════════════

def test_recreate_with_children(page_id):
    log.info("\n━━━ TEST 5: 기존 prepend 방식 — 토글 children 보존 검증 ━━━")

    try:
        # 최상위 블록 읽기
        resp = notion.blocks.children.list(block_id=page_id)
        top_blocks = resp.get("results", [])

        # heading_3 중 has_children=True인 블록 확인
        toggle_count = 0
        for b in top_blocks:
            if b["type"] == "heading_3" and b.get("has_children"):
                toggle_count += 1
                heading_data = b["heading_3"]
                # heading_3 응답에 children 키가 있는지?
                log.info(f"  toggle heading 발견: children 키 in response = {'children' in heading_data}")
                log.info(f"  → heading_3 응답 키: {list(heading_data.keys())}")

        if toggle_count == 0:
            log.info("  ⚠️ toggle heading이 없음 (TEST 1이 먼저 실행되어야 함)")
            return

        # 기존 코드 방식으로 블록을 dict로 변환 시도
        log.info(f"\n  [재생성 시뮬레이션]")
        old_blocks = []
        for block in top_blocks:
            btype = block["type"]
            if btype in ("paragraph", "heading_3", "divider"):
                reconstructed = {
                    "object": "block",
                    "type": btype,
                    btype: block[btype],
                }
                old_blocks.append(reconstructed)
                if btype == "heading_3":
                    has_ch = block.get("has_children", False)
                    ch_in_data = "children" in block[btype]
                    log.info(f"  → heading_3 재생성: has_children={has_ch}, children_in_data={ch_in_data}")
                    if has_ch and not ch_in_data:
                        log.warning("  ⚠️ children이 재생성 데이터에 포함되지 않음!")
                        log.warning("  → 현재 prepend 방식으로는 토글 내부 데이터가 소실됩니다")

        log.info(f"  재생성 대상 블록 수: {len(old_blocks)}")

    except Exception as e:
        log.error(f"  ❌ 실패: {e}")


# ══════════════════════════════════════════════════════════════════
# 메인 실행
# ══════════════════════════════════════════════════════════════════

def main():
    cleanup = "--cleanup" in sys.argv

    log.info("=" * 60)
    log.info("Notion API 토글 헤딩 + prepend 검증 스크립트")
    log.info("=" * 60)

    # 테스트용 페이지 생성
    log.info("\n📄 테스트 페이지 생성 중...")
    try:
        page = notion.pages.create(
            parent={"database_id": NOTION_TASK_DB_ID},
            properties={
                "업무명": {"title": [{"text": {"content": "🧪 API 검증용 (삭제 가능)"}}]},
                "진행 상황": {"status": {"name": "🙏 진행 예정"}},
            },
        )
        page_id = page["id"]
        log.info(f"  ✅ 테스트 페이지: {page['url']}")
    except Exception as e:
        log.error(f"  ❌ 테스트 페이지 생성 실패: {e}")
        sys.exit(1)

    time.sleep(0.5)

    # 테스트 실행
    try:
        heading_id = test_create_toggle_heading(page_id)
        time.sleep(0.35)
        test_read_toggle_heading(page_id, heading_id)
        time.sleep(0.35)
        test_after_parameter(page_id)
        time.sleep(0.35)
        test_position_start(page_id)
        time.sleep(0.35)
        test_recreate_with_children(page_id)
    except Exception as e:
        log.error(f"\n❌ 예상치 못한 오류: {e}")

    # 정리
    log.info("\n" + "=" * 60)
    if cleanup:
        log.info("🧹 테스트 페이지 삭제 중...")
        try:
            notion.pages.update(page_id=page_id, archived=True)
            log.info("  ✅ 삭제 완료")
        except Exception as e:
            log.error(f"  ❌ 삭제 실패: {e}")
    else:
        log.info(f"📌 테스트 페이지가 남아있습니다 (--cleanup 옵션으로 삭제)")
        log.info(f"   URL: {page['url']}")

    log.info("\n완료!")


if __name__ == "__main__":
    main()
