import json
import re
from typing import Dict, List, Optional

import config
from infra.redis_store import ensure_json, set_json
from llm import get_llm

SKILL_NAME = "config_monitor_skill"
SKILL_DESCRIPTION = "新增/删除盯盘默认规则（monitor_rules），支持批量操作"
RULES_KEY = "monitor_rules"

DEFAULT_RULES = [
    {"type": "price_change", "window": 6, "threshold": 0.01},
    {"type": "price_change", "window": 10, "threshold": 0.01},
    {"type": "volume_spike", "threshold": 2.0},
]

_parser_llm = get_llm(model=config.MODEL, temperature=0, timeout=18, max_retries=1)


def can_handle(text: str) -> bool:
    t = str(text or "")
    return bool(
        re.search(
            r"(新增规则|添加规则|配置规则|删除规则|移除规则|批量|monitor_rules|阈值|window|秒|放量|涨跌幅)",
            t,
            re.IGNORECASE,
        )
    )


def _extract_json_objects(text: str) -> List[dict]:
    s = str(text or "").strip()
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")

    out: List[dict] = []
    start = -1
    depth = 0
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    seg = s[start : i + 1]
                    try:
                        obj = json.loads(seg)
                        if isinstance(obj, dict):
                            out.append(obj)
                    except Exception:
                        pass
                    start = -1
    return out


def _parse_indexes_from_text(text: str) -> List[int]:
    t = str(text or "")
    if not re.search(r"(删除|移除)", t):
        return []

    idxs = set()

    m = re.search(r"第([0-9、,，\s\-~到至]+)条", t)
    if m:
        chunk = m.group(1)
        for a, b in re.findall(r"(\d+)\s*(?:-|~|到|至)\s*(\d+)", chunk):
            x, y = int(a), int(b)
            lo, hi = (x, y) if x <= y else (y, x)
            for n in range(lo, hi + 1):
                idxs.add(n)
        for n in re.findall(r"\d+", chunk):
            idxs.add(int(n))
        return sorted([x for x in idxs if x > 0])

    if re.search(r"规则", t):
        for n in re.findall(r"\d+", t):
            idxs.add(int(n))

    return sorted([x for x in idxs if x > 0])


def _extract_rules_from_text(text: str) -> List[Dict]:
    """基于正则从自然语言里提取多条规则，优先保证批量新增稳定性。"""
    t = str(text or "")
    rules: List[Dict] = []

    # 价格异动：例如“10秒涨跌幅超过1%”
    for m in re.finditer(r"(\d+)\s*秒[^。；;\n]*?(?:涨跌幅|涨幅|跌幅)[^0-9]*([0-9]+(?:\.[0-9]+)?)\s*%", t):
        window = int(m.group(1))
        pct = float(m.group(2))
        rules.append({"type": "price_change", "window": window, "threshold": pct / 100.0})

    # 放量：例如“放量超过2.6倍”
    for m in re.finditer(r"(?:放量|成交量)[^0-9]*([0-9]+(?:\.[0-9]+)?)\s*倍", t):
        ratio = float(m.group(1))
        rules.append({"type": "volume_spike", "threshold": ratio})

    # 去重（保持顺序）
    seen = set()
    unique = []
    for r in rules:
        key = json.dumps(r, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    return unique


def _extract_op_with_llm(text: str) -> Optional[dict]:
    prompt = f"""
你是规则操作解析助手。将用户意图解析为 JSON。
只输出 JSON，不要输出其它文本。

输出格式：
1) 批量新增：
{{"action":"add","rules":[{{"type":"price_change","window":10,"threshold":0.01}},{{"type":"volume_spike","threshold":2.5}}]}}
2) 批量删除（按序号）：
{{"action":"delete","indexes":[4,5,6]}}
3) 按规则删除：
{{"action":"delete","rules":[{{"type":"volume_spike","threshold":2.5}}]}}

说明：
- threshold 必须是小数，1% 写 0.01
- price_change 的 window 单位是秒

用户输入：{text}
"""
    try:
        out = str(_parser_llm.invoke(prompt).content).strip()
        objs = _extract_json_objects(out)
        if objs:
            return objs[0]
    except Exception:
        pass
    return None


def _validate_rule(rule: Dict) -> str:
    rule_type = str(rule.get("type", "")).strip().lower()
    if rule_type not in ("price_change", "volume_spike"):
        return "规则 type 仅支持 price_change / volume_spike"

    if rule_type == "price_change":
        try:
            window = int(rule.get("window", 0))
            threshold = float(rule.get("threshold", 0))
        except Exception:
            return "price_change 需要有效的 window(int, 单位秒) 与 threshold(float)"
        if window <= 0 or threshold <= 0:
            return "price_change 的 window(秒)/threshold 必须 > 0"

    if rule_type == "volume_spike":
        try:
            threshold = float(rule.get("threshold", 0))
        except Exception:
            return "volume_spike 需要有效的 threshold(float)"
        if threshold <= 0:
            return "volume_spike 的 threshold 必须 > 0"

    return ""


def _normalize_rule(rule: Dict) -> str:
    return json.dumps(rule, ensure_ascii=False, sort_keys=True)


def _build_op(text: str) -> Optional[dict]:
    t = str(text or "")
    objs = _extract_json_objects(t)

    if objs and any(k in objs[0] for k in ("action", "indexes", "rules")):
        op = objs[0]
        action = str(op.get("action", "")).strip().lower()
        if action in ("add", "delete"):
            return op

    if re.search(r"(删除|移除)", t) and re.search(r"规则", t):
        idxs = _parse_indexes_from_text(t)
        if idxs:
            return {"action": "delete", "indexes": idxs}

    if objs:
        candidate_rules = [x for x in objs if isinstance(x, dict) and str(x.get("type", "")).strip()]
        if candidate_rules:
            return {"action": "add", "rules": candidate_rules}

    # 先做确定性自然语言提取（支持一次提取多条）
    direct_rules = _extract_rules_from_text(t)
    if direct_rules:
        return {"action": "add", "rules": direct_rules}

    return _extract_op_with_llm(t)


def run(text: str, context=None):
    op = _build_op(text)
    if not isinstance(op, dict):
        return "请说明要新增或删除哪些规则。示例：删除第4、5条规则；或 新增10秒涨跌幅超过1%提醒。"

    action = str(op.get("action", "")).strip().lower()
    rules: List[Dict] = ensure_json(RULES_KEY, DEFAULT_RULES)
    if not isinstance(rules, list):
        rules = list(DEFAULT_RULES)

    if action == "add":
        add_rules = op.get("rules")
        if isinstance(add_rules, dict):
            add_rules = [add_rules]
        if not isinstance(add_rules, list) or not add_rules:
            return "❌ 未识别到可新增的规则。"

        existing = {_normalize_rule(r) for r in rules if isinstance(r, dict)}
        added = 0
        errors = []
        for idx, rule in enumerate(add_rules, 1):
            if not isinstance(rule, dict):
                errors.append(f"第{idx}条不是有效对象")
                continue
            err = _validate_rule(rule)
            if err:
                errors.append(f"第{idx}条校验失败: {err}")
                continue
            key = _normalize_rule(rule)
            if key in existing:
                continue
            rules.append(rule)
            existing.add(key)
            added += 1

        if not set_json(RULES_KEY, rules):
            return "❌ 保存规则失败：Redis 不可用"

        msg = f"✅ 已新增 {added} 条规则，当前共 {len(rules)} 条。"
        if errors:
            msg += "\n" + "；".join(errors)
        return msg

    if action == "delete":
        idxs = op.get("indexes")
        del_rules = op.get("rules")

        removed = 0
        misses = []

        if isinstance(idxs, list) and idxs:
            valid_idxs = sorted({int(x) for x in idxs if str(x).isdigit() and int(x) > 0}, reverse=True)
            for i in valid_idxs:
                pos = i - 1
                if 0 <= pos < len(rules):
                    rules.pop(pos)
                    removed += 1
                else:
                    misses.append(str(i))

        elif isinstance(del_rules, list) and del_rules:
            targets = {_normalize_rule(r) for r in del_rules if isinstance(r, dict)}
            kept = []
            for r in rules:
                if isinstance(r, dict) and _normalize_rule(r) in targets:
                    removed += 1
                    continue
                kept.append(r)
            rules = kept
        else:
            return "❌ 删除操作缺少 indexes 或 rules。"

        if removed == 0 and misses:
            return f"⚠️ 未删除任何规则：序号 {','.join(misses)} 不存在。"

        if not set_json(RULES_KEY, rules):
            return "❌ 保存规则失败：Redis 不可用"

        msg = f"✅ 已删除 {removed} 条规则，当前共 {len(rules)} 条。"
        if misses:
            msg += f"（以下序号不存在：{','.join(misses)}）"
        return msg

    return "❌ 未识别操作类型，请使用新增或删除规则。"
