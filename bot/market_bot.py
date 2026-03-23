import threading

import config

from agents import brain_agent
from agents.market_agent import MarketAgent
from infra.redis_store import ensure_json
from monitor.worker import DEFAULT_RULES, MonitorWorker


_BOT_LOCK = threading.Lock()
_BOT_INSTANCE = None


class MarketSystem:
    """
    市场盯盘系统协调器（仅市场事件）：
    - MonitorWorker：规则检测+事件入队
    - MarketAgent：市场事件消费并交给 brain_agent 决策
    """

    def __init__(self):
        self.monitor = MonitorWorker()
        self.market_agent = MarketAgent(event_handler=brain_agent.handle_event)

    def start(self):
        # 启动时确保 Redis 中存在默认规则和监控列表键。
        ensure_json("monitor_rules", DEFAULT_RULES)
        ensure_json("watchlist", list(getattr(config, "STOCK_LIST", [])))

        self.monitor.start()
        self.market_agent.start()
        print("[market_system] monitor worker started", flush=True)
        print("[market_system] market agent started", flush=True)

    def stop(self):
        self.monitor.stop()
        self.market_agent.stop()
        print("[market_system] stop requested", flush=True)


def start_market_watcher():
    global _BOT_INSTANCE
    enabled = bool(getattr(config, "MONITOR_ENABLED", True))
    if not enabled:
        print("[market_system] disabled by config", flush=True)
        return None

    with _BOT_LOCK:
        if _BOT_INSTANCE is None:
            _BOT_INSTANCE = MarketSystem()
            _BOT_INSTANCE.start()
        return _BOT_INSTANCE
