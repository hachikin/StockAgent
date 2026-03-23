import re

from infra.redis_store import ensure_json

SKILL_NAME = "view_monitor_rules_skill"
SKILL_DESCRIPTION = "查看当前盯盘默认规则（monitor_rules）"
RULES_KEY = "monitor_rules"

DEFAULT_RULES = [
    {"type": "price_change", "window": 6, "threshold": 0.01},
    {"type": "price_change", "window": 10, "threshold": 0.01},
    {"type": "volume_spike", "threshold": 2.0},
]


def can_handle(text: str) -> bool:
    t = str(text or "")
    return bool(re.search(r"(查看规则|规则列表|monitor_rules|当前规则)", t, re.IGNORECASE))


def _format_rule(i: int, rule: dict) -> str:
    t = str(rule.get("type", "")).strip().lower()

    if t == "price_change":
        window = int(rule.get("window", 0) or 0)
        threshold = float(rule.get("threshold", 0) or 0)
        pct = threshold * 100.0
        return f"{i}. 价格异动规则：{window}秒内涨跌幅达到 {pct:.2f}% 时触发提醒。"

    if t == "volume_spike":
        threshold = float(rule.get("threshold", 0) or 0)
        return f"{i}. 成交量异动规则：成交量达到近一段均量的 {threshold:.2f} 倍时触发提醒。"

    return f"{i}. 其他规则：{rule}"


def run(text: str, context=None):
    rules = ensure_json(RULES_KEY, DEFAULT_RULES)
    if not isinstance(rules, list) or not rules:
        return "当前还没有可用的盯盘规则。"

    lines = [
        f"当前共有 {len(rules)} 条盯盘规则：",
        "说明：价格异动规则中的时间窗口单位为秒。",
    ]

    idx = 1
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        lines.append(_format_rule(idx, rule))
        idx += 1

    lines.append("如果你要我帮你删改，直接说“删除第4条规则”或“新增10秒涨跌幅超过1%提醒”就可以。")
    return "\n".join(lines)
