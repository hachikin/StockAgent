import json
import threading
from datetime import date, datetime
from typing import Any, Optional

import redis

import config

_REDIS_LOCK = threading.Lock()
_REDIS_CLIENT = None


def _json_default(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if hasattr(obj, "item"):
        try:
            return obj.item()
        except Exception:
            pass
    if hasattr(obj, "isoformat"):
        try:
            return obj.isoformat()
        except Exception:
            pass
    return str(obj)


def get_redis() -> Optional[redis.Redis]:
    """Lazy 初始化 Redis 连接，失败时返回 None，避免业务线程直接崩溃。"""
    global _REDIS_CLIENT
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT

    with _REDIS_LOCK:
        if _REDIS_CLIENT is not None:
            return _REDIS_CLIENT
        try:
            client = redis.Redis(
                host=getattr(config, "REDIS_HOST", "localhost"),
                port=int(getattr(config, "REDIS_PORT", 6379)),
                db=int(getattr(config, "REDIS_DB", 0)),
                socket_timeout=float(getattr(config, "REDIS_SOCKET_TIMEOUT", 2.0)),
                decode_responses=True,
            )
            client.ping()
            _REDIS_CLIENT = client
            return _REDIS_CLIENT
        except Exception:
            return None


def get_json(key: str, default: Any = None):
    client = get_redis()
    if not client:
        return default
    try:
        val = client.get(key)
        if val is None:
            return default
        return json.loads(val)
    except Exception:
        return default


def set_json(key: str, value: Any, ex: Optional[int] = None) -> bool:
    client = get_redis()
    if not client:
        return False
    try:
        payload = json.dumps(value, ensure_ascii=False, default=_json_default)
        if ex is None:
            client.set(key, payload)
        else:
            client.set(key, payload, ex=int(ex))
        return True
    except Exception:
        return False


def lpush_json(key: str, value: Any) -> bool:
    client = get_redis()
    if not client:
        return False
    try:
        payload = json.dumps(value, ensure_ascii=False, default=_json_default)
        client.lpush(key, payload)
        return True
    except Exception:
        return False


def rpop_json(key: str, default: Any = None):
    client = get_redis()
    if not client:
        return default
    try:
        payload = client.rpop(key)
        if payload is None:
            return default
        return json.loads(payload)
    except Exception:
        return default


def exists(key: str) -> bool:
    client = get_redis()
    if not client:
        return False
    try:
        return bool(client.exists(key))
    except Exception:
        return False


def set_text(key: str, value: str, ex: Optional[int] = None) -> bool:
    client = get_redis()
    if not client:
        return False
    try:
        if ex is None:
            client.set(key, value)
        else:
            client.set(key, value, ex=int(ex))
        return True
    except Exception:
        return False


def ensure_json(key: str, default_value: Any) -> Any:
    """获取 JSON，不存在则写入默认值并返回默认值。"""
    val = get_json(key, default=None)
    if val is not None:
        return val
    set_json(key, default_value)
    return default_value
