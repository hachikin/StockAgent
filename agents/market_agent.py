import threading
from typing import Any, Callable, Dict

import config

from events.queue import pop_event


class MarketAgent:
    """
    市场事件消费线程（事件驱动）：
    - 轮询市场事件队列
    - 将事件规范化后交给 brain_agent 处理
    """

    def __init__(self, event_handler: Callable[[Dict[str, Any]], Dict[str, Any]]):
        self.event_handler = event_handler
        self.poll_seconds = int(getattr(config, "MARKET_AGENT_POLL_SECONDS", 6))
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="market-agent", daemon=True)

    def start(self):
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self):
        self._stop_event.set()

    @staticmethod
    def _to_market_signal(event: Dict[str, Any]) -> Dict[str, Any]:
        code = str(event.get("code", "")).strip()
        event_type = str(event.get("event_type", "")).strip().lower()

        sentiment = "neutral"
        if event_type == "price_change":
            try:
                v = float(event.get("value", 0) or 0)
                sentiment = "bullish" if v > 0 else "bearish"
            except Exception:
                sentiment = "neutral"

        reason = (
            f"market_event={event_type} window={event.get('window', '')} "
            f"value={event.get('value', '')}"
        )

        return {
            "type": "market_signal",
            "stocks": [code] if code else [],
            "sentiment": sentiment,
            "reason": reason,
            "confidence": 0.65,
            "is_event": True,
            "raw_event": event,
        }

    def run_once(self):
        event = pop_event()
        if not isinstance(event, dict):
            return

        signal = self._to_market_signal(event)
        if not signal.get("stocks"):
            return

        try:
            self.event_handler(signal)
        except Exception as e:
            print(f"[market_agent] handle_event failed: {e.__class__.__name__}", flush=True)

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as e:
                print(f"[market_agent] run_once failed: {e.__class__.__name__}", flush=True)
            self._stop_event.wait(self.poll_seconds)
