import json
import os

import config
from infra.redis_store import ensure_json, set_json
from tools.tushare_tool import resolve_stock

FILE = getattr(config, "STOCK_FILE", "storage/watchlist.json")
WATCHLIST_KEY = "watchlist"


def _load_from_file():
    if not os.path.exists(FILE):
        return []
    try:
        with open(FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def load_watchlist():
    """监控列表统一读 Redis；若 Redis 首次为空则从旧 JSON 迁移一次。"""
    fallback = _load_from_file()
    data = ensure_json(WATCHLIST_KEY, fallback)
    if isinstance(data, list):
        return [str(x).strip() for x in data if str(x).strip()]
    return fallback


def save_watchlist(data):
    cleaned = [str(x).strip() for x in (data or []) if str(x).strip()]
    set_json(WATCHLIST_KEY, cleaned)


def _normalize_watch_item(raw_name):
    raw = str(raw_name or "").strip()
    if not raw:
        return None

    resolved = resolve_stock(raw)
    if resolved.get("ts_code"):
        if raw.isdigit() or raw.upper().endswith(".SZ") or raw.upper().endswith(".SH"):
            return resolved.get("ts_code")
        return resolved.get("name") or raw

    return None


def add_stock(name):
    normalized = _normalize_watch_item(name)
    if not normalized:
        return f"❌ 未找到股票：{name}，请检查名称或代码是否正确"

    data = load_watchlist()
    if normalized in data:
        return f"{normalized} 已经在监控列表中"

    data.append(normalized)
    save_watchlist(data)
    return f"✅ 已加入 {normalized}"


def remove_stock(name):
    normalized = _normalize_watch_item(name) or str(name or "").strip()
    data = load_watchlist()
    if normalized not in data:
        return f"{normalized} 不在列表中"

    data.remove(normalized)
    save_watchlist(data)
    return f"🗑️ 已删除 {normalized}"


def list_stock():
    data = load_watchlist()
    if not data:
        return "当前没有监控股票"
    return "监控列表：\n" + "\n".join(data)
