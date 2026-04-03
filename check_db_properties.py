"""
check_db_properties.py — Task DB 속성 조회 스크립트
=====================================================
노션 뷰 5종 세팅 전 현재 DB 속성 상태를 확인합니다.

실행: cd bot && python check_db_properties.py
"""

from config import NOTION_TOKEN, NOTION_TASK_DB_ID
from notion_client import Client

notion = Client(auth=NOTION_TOKEN)

# 뷰 5종에 필요한 속성
REQUIRED_FOR_VIEWS = {
    "업무명":    "뷰1(용역별 히스토리) 그룹핑",
    "마감일자":  "뷰2(마감 캘린더) 날짜 속성, 뷰5(마감 리스크) D-7 필터",
    "발주처":    "뷰3(발주처별) 그룹핑",
    "담당자":    "뷰4(작성자별) 그룹핑",
    "마감리스크": "뷰5(마감 리스크) 체크박스 필터",
}


def main():
    db = notion.databases.retrieve(database_id=NOTION_TASK_DB_ID)
    props = db.get("properties", {})

    print("=" * 60)
    print(f"DB: {db.get('title', [{}])[0].get('plain_text', '(제목 없음)')}")
    print(f"ID: {NOTION_TASK_DB_ID}")
    print("=" * 60)

    print("\n📋 현재 DB 속성 목록:")
    for name, info in sorted(props.items()):
        ptype = info.get("type", "?")
        print(f"  - {name} ({ptype})")

    print("\n🔍 뷰 5종 필수 속성 점검:")
    all_ok = True
    for prop_name, usage in REQUIRED_FOR_VIEWS.items():
        exists = prop_name in props
        status = "✅" if exists else "❌ 누락"
        ptype = props[prop_name]["type"] if exists else "-"
        print(f"  {status} {prop_name} ({ptype}) — {usage}")
        if not exists:
            all_ok = False

    if all_ok:
        print("\n✅ 모든 필수 속성이 존재합니다. 뷰 세팅을 진행하세요.")
    else:
        print("\n⚠️  누락 속성이 있습니다. 봇을 재시작하면 자동으로 추가됩니다.")
        print("   (ensure_db_properties() 함수가 앱 시작 시 실행)")


if __name__ == "__main__":
    main()
