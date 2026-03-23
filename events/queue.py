import json
from typing import Any, Dict, List, Optional

from infra.redis_store import get_redis, lpush_json, rpop_json

MARKET_EVENT_QUEUE_KEY = "market_events"
NEWS_QUEUE_KEY = "news_queue"


def push_event(event: Dict[str, Any]) -> bool:
    """统一事件入队：monitor 只负责生产事件，不负责消费分析。"""
    if not isinstance(event, dict):
        return False
    return lpush_json(MARKET_EVENT_QUEUE_KEY, event)


def pop_event() -> Optional[Dict[str, Any]]:
    """统一事件出队：market_agent 消费并分析。"""
    data = rpop_json(MARKET_EVENT_QUEUE_KEY, default=None)
    if isinstance(data, dict):
        return data
    return None


def push_news(news_item: Dict[str, Any]) -> bool:
    """新闻入队：news_bot 只生产原始新闻事件。"""
    if not isinstance(news_item, dict):
        return False
    return lpush_json(NEWS_QUEUE_KEY, news_item)


def get_news_window(limit: int = 20) -> List[Dict[str, Any]]:
    """获取新闻窗口，不出队，用于重叠窗口批量分析。"""
    client = get_redis()
    if not client:
        return []

    n = max(1, int(limit))
    try:
        payloads = client.lrange(NEWS_QUEUE_KEY, 0, n - 1)
    except Exception:
        return []

    rows: List[Dict[str, Any]] = []
    for payload in payloads or []:
        try:
            item = json.loads(payload)
            if isinstance(item, dict):
                rows.append(item)
        except Exception:
            continue
    return rows


def trim_news_processed(processed: int = 15) -> bool:
    """移除已处理新闻，保留重叠窗口尾部样本。"""
    client = get_redis()
    if not client:
        return False

    k = max(0, int(processed))
    try:
        client.ltrim(NEWS_QUEUE_KEY, k, -1)
        return True
    except Exception:
        return False


def get_news_queue_len() -> int:
    client = get_redis()
    if not client:
        return 0
    try:
        return int(client.llen(NEWS_QUEUE_KEY) or 0)
    except Exception:
        return 0
