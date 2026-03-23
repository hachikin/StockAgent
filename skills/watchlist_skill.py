import json
import re
import config

from llm import get_llm

from tools.watchlist_tool import add_stock, list_stock, remove_stock

SKILL_NAME = "watchlist_skill"
SKILL_DESCRIPTION = "监控列表增删查能力"

llm = get_llm(model=config.MODEL, temperature=0, timeout=20, max_retries=1)


ACTION_PATTERN = re.compile(r"(加入|添加|关注|删除|移除|取消关注|查看|列表|清单|watchlist|list|add|remove|del)", re.IGNORECASE)


def can_handle(text: str) -> bool:
    return bool(ACTION_PATTERN.search(str(text or "")))


def _normalize_stock(raw: str) -> str:
    s = str(raw or "").strip()
    s = re.sub(r"^(把|将)", "", s)
    s = re.sub(r"(加入|添加|关注|删除|移除|取消关注)", "", s)
    s = re.sub(r"(到|从)?监控列表$", "", s)
    return s.strip(" ：:，,。")


def _rule_parse(text: str):
    t = str(text or "").strip()

    m_add = re.search(r"(?:把|将)?(.+?)(?:加入|添加|关注)(?:到)?监控列表?", t)
    if m_add:
        return {"action": "add", "stock": _normalize_stock(m_add.group(1))}

    m_remove = re.search(r"(?:把|将)?(.+?)(?:删除|移除|取消关注)(?:从)?监控列表?", t)
    if m_remove:
        return {"action": "remove", "stock": _normalize_stock(m_remove.group(1))}

    add_keys = ["加入", "添加", "关注", "add"]
    remove_keys = ["删除", "移除", "取消关注", "remove", "del"]

    for k in add_keys:
        if k in t:
            stock = _normalize_stock(t.replace(k, ""))
            return {"action": "add", "stock": stock}

    for k in remove_keys:
        if k in t:
            stock = _normalize_stock(t.replace(k, ""))
            return {"action": "remove", "stock": stock}

    if any(k in t for k in ["查看", "列表", "清单", "list", "watchlist"]):
        return {"action": "list", "stock": ""}

    return {"action": "unknown", "stock": ""}


def parse_intent(text: str):
    data = _rule_parse(text)
    if data.get("action") != "unknown":
        return data

    prompt = f"""
你是监控列表管理助手。把用户输入解析成JSON。
只输出JSON，禁止markdown代码块。

输出：
{{"action":"add|remove|list|unknown","stock":"可空"}}

用户输入："{text}"
"""

    try:
        result = llm.invoke(prompt).content
        llm_data = json.loads(result)
        return {
            "action": str(llm_data.get("action", "unknown")).lower(),
            "stock": _normalize_stock(llm_data.get("stock", "")),
        }
    except Exception:
        return data


def handle_action(action: str, stock: str = ""):
    action = str(action or "").strip().lower()
    stock = str(stock or "").strip()

    if action == "add":
        if not stock:
            return "请告诉我要添加的股票代码或名称"
        return add_stock(stock)

    if action == "remove":
        if not stock:
            return "请告诉我要删除的股票代码或名称"
        return remove_stock(stock)

    if action == "list":
        return list_stock()

    return "我没理解你的意思 😅"


def run(text: str, context=None):
    parsed = parse_intent(text)
    return handle_action(parsed.get("action", "unknown"), parsed.get("stock", ""))
