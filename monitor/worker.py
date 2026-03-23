import hashlib
import json
import threading
import time
from collections import deque
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import config

from events.queue import push_event
from infra.redis_store import ensure_json, exists, set_text
from notifier.market_notify import send_quick_alert
from tools.get_price import get_realtime_price

try:
    from tools.tushare_tool import resolve_stock
except Exception:
    def resolve_stock(raw):
        code = str(raw or "").strip().upper()
        return {"ts_code": code, "name": code}


WATCHLIST_KEY = "watchlist"
RULES_KEY = "monitor_rules"
DEDUP_PREFIX = "event_dedup"
DEDUP_TTL_SECONDS = 60*20  # 同一股票+同一规则 20 分钟内只触发一次。

DEFAULT_RULES = [
    {"type": "price_change", "window": 6, "threshold": 0.01},
    {"type": "price_change", "window": 10, "threshold": 0.01},
    {"type": "volume_spike", "threshold": 2.0},
]

_UTC_TZ = ZoneInfo("UTC")
_CN_TZ = ZoneInfo("Asia/Shanghai")
_US_TZ = ZoneInfo("America/New_York")


class MonitorWorker:
    """
    Rule-based 盯盘线程。
    只做检测和事件投递，不调用 LLM，确保高频监控链路轻量稳定。
    """

    def __init__(self):
        self.interval_seconds = int(getattr(config, "MONITOR_INTERVAL_SECONDS", 6))
        self.heartbeat_seconds = int(getattr(config, "MONITOR_HEARTBEAT_SECONDS", 60))

        root_dir = Path(__file__).resolve().parent.parent
        default_log_file = root_dir / "log" / "markerrtwatchor_log"
        self.heartbeat_log_file = Path(getattr(config, "MARKET_HEARTBEAT_LOG_FILE", str(default_log_file)))

        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="monitor-worker", daemon=True)
        self._history: Dict[str, deque] = {}
        self._scan_alerts = 0
        self._scan_cycle = 0
        self._last_scan_stats: Dict[str, Any] = {}
        self._last_heartbeat_ts = 0.0
        self._last_quotes: Dict[str, Dict[str, Any]] = {}

    def start(self):
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _log_line(self, line: str):
        print(line, flush=True)
        try:
            self.heartbeat_log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.heartbeat_log_file.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self.scan_once()
            except Exception as e:
                self._log_line(f"[monitor_worker] scan error: {e.__class__.__name__}: {e}")

            self._emit_heartbeat()
            self._stop_event.wait(self.interval_seconds)

    def _emit_heartbeat(self):
        now = time.time()
        if now - self._last_heartbeat_ts < self.heartbeat_seconds:
            return

        self._last_heartbeat_ts = now
        stats = self._last_scan_stats or {}
        last_scan_ts = float(stats.get("last_scan_ts", 0) or 0)
        scan_age = int(now - last_scan_ts) if last_scan_ts > 0 else -1

        hb = (
            "[market_bot] heartbeat "
            f"cycle={self._scan_cycle} "
            f"watch_items={stats.get('watch_items', 0)} "
            f"open_items={stats.get('trading_items', 0)} "
            f"alerts={stats.get('alerts', 0)} "
            f"price_updates={stats.get('price_updates', 0)} "
            f"last_scan_age={scan_age}s "
            f"interval={self.interval_seconds}s"
        )
        self._log_line(hb)

        if not self._last_quotes:
            self._log_line("[market_bot] price_snapshot no_open_quotes")
            return

        parts = []
        for code in sorted(self._last_quotes.keys()):
            q = self._last_quotes.get(code, {})
            label = str(q.get("label", code))
            market = str(q.get("market", "UNKNOWN"))
            price = float(q.get("price", 0) or 0)
            src = str(q.get("source", "unknown"))
            age = int(max(0, now - float(q.get("ts", now))))
            change_pct = q.get("change_pct")
            if change_pct is None:
                change_text = "n/a"
            else:
                change_text = f"{float(change_pct) * 100:.2f}%"

            parts.append(
                f"{label}({code}) market={market} price={price:.4f} change={change_text} src={src} age={age}s"
            )

        self._log_line("[market_bot] price_snapshot " + " ; ".join(parts))

    def _load_watchlist(self) -> List[str]:
        fallback = list(getattr(config, "STOCK_LIST", []))
        watchlist = ensure_json(WATCHLIST_KEY, fallback)
        if isinstance(watchlist, list):
            return [str(x).strip() for x in watchlist if str(x).strip()]
        return fallback

    def _load_rules(self) -> List[Dict[str, Any]]:
        rules = ensure_json(RULES_KEY, DEFAULT_RULES)
        if isinstance(rules, list):
            return [r for r in rules if isinstance(r, dict)]
        return list(DEFAULT_RULES)

    @staticmethod
    def _rule_hash(rule: Dict[str, Any]) -> str:
        payload = json.dumps(rule, ensure_ascii=False, sort_keys=True)
        return hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _guess_market(code: str) -> str:
        c = str(code or "").strip().upper()
        if c.endswith(".SH") or c.endswith(".SZ"):
            return "CN"
        if c.endswith(".US"):
            return "US"
        if c.isdigit() and len(c) == 6:
            return "CN"
        if c.isalpha() and 1 <= len(c) <= 8:
            return "US"
        return "UNKNOWN"

    @staticmethod
    def _is_cn_trading(now_cn: datetime) -> bool:
        if now_cn.weekday() >= 5:
            return False
        t = now_cn.time()
        am = dt_time(9, 30) <= t <= dt_time(11, 30)
        pm = dt_time(13, 0) <= t <= dt_time(15, 0)
        return am or pm

    @staticmethod
    def _is_us_trading(now_us: datetime) -> bool:
        if now_us.weekday() >= 5:
            return False
        t = now_us.time()
        return dt_time(9, 30) <= t <= dt_time(16, 0)

    def _is_market_open(self, market: str, now_utc: datetime | None = None) -> bool:
        now_utc = now_utc or datetime.now(_UTC_TZ)
        m = str(market or "").strip().upper()

        if m == "CN":
            return self._is_cn_trading(now_utc.astimezone(_CN_TZ))
        if m == "US":
            return self._is_us_trading(now_utc.astimezone(_US_TZ))
        return False

    def _check_and_mark_dedup(self, code: str, rule: Dict[str, Any]) -> bool:
        """同一股票+同一规则 5 分钟内只触发一次。"""
        dedup_key = f"{DEDUP_PREFIX}:{code}:{self._rule_hash(rule)}"
        if exists(dedup_key):
            return False
        set_text(dedup_key, "1", ex=DEDUP_TTL_SECONDS)
        return True

    def _trigger_event(self, event: Dict[str, Any], rule: Dict[str, Any]):
        if not self._check_and_mark_dedup(event.get("code", ""), rule):
            return

        send_quick_alert(event)
        push_event(event)
        self._scan_alerts += 1

    def _price_change_event(self, code: str, rule: Dict[str, Any], now_ts: float, samples: deque):
        # window 的单位是秒，不是日。
        window = int(rule.get("window", 0) or 0)
        threshold = float(rule.get("threshold", 0) or 0)
        if window <= 0 or threshold <= 0:
            return

        base = None
        latest = samples[-1] if samples else None
        if not latest:
            return

        latest_price = float(latest.get("price", 0) or 0)
        if latest_price <= 0:
            return

        for item in samples:
            age = now_ts - float(item.get("ts", now_ts))
            if age <= window:
                base = item
                break

        if not base:
            return

        base_price = float(base.get("price", 0) or 0)
        if base_price <= 0:
            return

        change = (latest_price - base_price) / base_price
        if abs(change) < threshold:
            return

        event = {
            "code": code,
            "event_type": "price_change",
            "window": window,
            "threshold": threshold,
            "value": change,
            "timestamp": int(now_ts),
        }
        self._trigger_event(event, rule)

    def _volume_spike_event(self, code: str, rule: Dict[str, Any], now_ts: float, samples: deque):
        threshold = float(rule.get("threshold", 0) or 0)
        if threshold <= 0 or len(samples) < 2:
            return

        latest = samples[-1]
        latest_vol = float(latest.get("volume", 0) or 0)
        if latest_vol <= 0:
            return

        hist = [float(x.get("volume", 0) or 0) for x in list(samples)[:-1] if float(x.get("volume", 0) or 0) > 0]
        if not hist:
            return

        avg = sum(hist) / len(hist)
        if avg <= 0:
            return

        ratio = latest_vol / avg
        if ratio < threshold:
            return

        event = {
            "code": code,
            "event_type": "volume_spike",
            "threshold": threshold,
            "value": ratio,
            "timestamp": int(now_ts),
        }
        self._trigger_event(event, rule)

    def rule_engine(self, code: str, rules: List[Dict[str, Any]], market_data: Dict[str, Any]):
        """统一规则引擎：循环执行所有规则，禁止写死单一条件。"""
        samples = self._history.setdefault(code, deque(maxlen=120))
        samples.append(market_data)

        now_ts = float(market_data.get("ts", time.time()))
        for rule in rules:
            rule_type = str(rule.get("type", "")).strip().lower()
            if rule_type == "price_change":
                self._price_change_event(code, rule, now_ts, samples)
            elif rule_type == "volume_spike":
                self._volume_spike_event(code, rule, now_ts, samples)

    def scan_once(self):
        watchlist = self._load_watchlist()
        rules = self._load_rules()
        now_ts = time.time()
        now_utc = datetime.now(_UTC_TZ)

        self._scan_cycle += 1
        self._scan_alerts = 0
        watch_items = len(watchlist)
        trading_items = 0
        price_updates = 0
        scan_quotes: Dict[str, Dict[str, Any]] = {}

        for raw in watchlist:
            resolved = resolve_stock(raw)
            code = str(resolved.get("ts_code") or raw).strip().upper()
            if not code:
                continue

            label = str(resolved.get("name") or raw)
            market = self._guess_market(code)
            if not self._is_market_open(market, now_utc=now_utc):
                continue
            trading_items += 1

            quote = get_realtime_price(code)
            if not quote:
                continue

            try:
                price = float(quote.get("price", 0) or 0)
                volume = float(quote.get("volume", 0) or 0)
                prev_close = float(quote.get("prev_close", 0) or 0)
            except Exception:
                continue

            if price <= 0:
                continue
            price_updates += 1

            change_pct = None
            if prev_close > 0:
                change_pct = (price - prev_close) / prev_close

            scan_quotes[code] = {
                "label": label,
                "market": market,
                "price": price,
                "change_pct": change_pct,
                "source": quote.get("source", "unknown"),
                "ts": now_ts,
            }

            market_data = {
                "ts": now_ts,
                "price": price,
                "volume": volume,
                "source": quote.get("source", "unknown"),
                "market": market,
                "dt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.rule_engine(code, rules, market_data)

        self._last_quotes = scan_quotes
        self._last_scan_stats = {
            "last_scan_ts": now_ts,
            "watch_items": watch_items,
            "trading_items": trading_items,
            "price_updates": price_updates,
            "alerts": self._scan_alerts,
            "rules": len(rules),
        }
