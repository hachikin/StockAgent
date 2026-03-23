import time
from typing import Any, Dict

from infra.redis_store import exists, set_text
from notifier.feishu import send


QUICK_LIMIT_SECONDS = 3
ANALYSIS_LIMIT_SECONDS = 3


def _allow_send(channel: str, cooldown_seconds: int) -> bool:
    """用 Redis TTL 做轻量限流，防止高频触发刷屏。"""
    key = f"feishu_rate:{channel}"
    if exists(key):
        return False
    set_text(key, "1", ex=cooldown_seconds)
    return True


def send_quick_alert(event: Dict[str, Any]) -> bool:
    if not _allow_send("quick", QUICK_LIMIT_SECONDS):
        return False

    code = str(event.get("code", ""))
    event_type = str(event.get("event_type", ""))
    window = event.get("window")
    value = float(event.get("value", 0) or 0)
    pct = value * 100.0 if event_type == "price_change" else value

    if event_type == "price_change":
        msg = f"🚨 {code} {window}秒涨跌幅 {pct:.2f}%"
    elif event_type == "volume_spike":
        msg = f"🚨 {code} 成交量放大至均值 {pct:.2f} 倍"
    else:
        msg = f"🚨 {code} 出现异动"

    return send(msg)


def send_analysis(result: str, event: Dict[str, Any] | None = None) -> bool:
    if not _allow_send("analysis", ANALYSIS_LIMIT_SECONDS):
        return False

    ts = int(time.time())
    event_head = ""
    if isinstance(event, dict) and event:
        event_head = f"[事件] {event.get('code', '')} {event.get('event_type', '')} @ {event.get('timestamp', ts)}\n"

    return send(f"{event_head}{str(result or '').strip()}")
