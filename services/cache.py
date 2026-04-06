# -*- coding: utf-8 -*-
"""
services/cache.py — 간단한 In-memory TTL 캐시

Notion API 및 Slack users_info 호출 결과를 일정 시간 동안 보관하여
반복 호출을 줄입니다.
"""

import time
import threading

_lock = threading.Lock()
_store: dict[str, dict] = {}


def get(key: str):
    """캐시에서 값 조회. 만료됐으면 None 반환."""
    with _lock:
        entry = _store.get(key)
        if entry and time.time() < entry["expires"]:
            return entry["value"]
        if entry:
            del _store[key]
        return None


def set(key: str, value, ttl: float):
    """캐시에 값 저장. ttl은 초 단위."""
    with _lock:
        _store[key] = {"value": value, "expires": time.time() + ttl}


def delete(key: str):
    """캐시 항목 삭제."""
    with _lock:
        _store.pop(key, None)


def clear_prefix(prefix: str):
    """특정 prefix로 시작하는 모든 항목 삭제."""
    with _lock:
        keys = [k for k in _store if k.startswith(prefix)]
        for k in keys:
            del _store[k]
