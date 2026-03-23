"""Brain 对话层。

职责：
1) 管理 skill 动态加载与路由决策。
2) 管理会话记忆、摘要和多步 skill 编排。
3) 暴露 handle_user_message 作为对话入口。
"""

import importlib
import importlib.util
import json
import re
import threading
import time
from pathlib import Path

import config

from infra.redis_store import exists, get_json, set_json, set_text
from llm import get_llm
from notifier.feishu import send


class SkillManager:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir

    def load_skills(self):
        importlib.invalidate_caches()
        skills = {}

        if not self.skills_dir.exists():
            return skills

        for file_path in sorted(self.skills_dir.glob("*.py")):
            if file_path.name.startswith("_") or file_path.name == "__init__.py":
                continue

            module_name = f"skills.dynamic_{file_path.stem}_{file_path.stat().st_mtime_ns}"
            spec = importlib.util.spec_from_file_location(module_name, str(file_path))
            if not spec or not spec.loader:
                continue

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            skill_name = getattr(module, "SKILL_NAME", file_path.stem)
            skill_desc = getattr(module, "SKILL_DESCRIPTION", "")
            run_func = getattr(module, "run", None)
            can_handle = getattr(module, "can_handle", None)

            if callable(run_func):
                skills[skill_name] = {
                    "name": skill_name,
                    "description": skill_desc,
                    "run": run_func,
                    "can_handle": can_handle if callable(can_handle) else None,
                }

        return skills


_SKILL_MANAGER = SkillManager(Path(__file__).resolve().parent.parent / "skills")
_router_llm = get_llm(model=config.MODEL, temperature=0, timeout=12, max_retries=1)
_chat_llm = get_llm(model=config.MODEL, temperature=0.3, timeout=30, max_retries=1)
_summary_llm = get_llm(model=config.MODEL, temperature=0, timeout=15, max_retries=1)

CHAT_WINDOW_SIZE = int(getattr(config, "BRAIN_CHAT_WINDOW", 4))
CHAT_MEM_TTL = int(getattr(config, "BRAIN_CHAT_TTL_SECONDS", 7 * 24 * 3600))
SUMMARY_BATCH_SIZE = int(getattr(config, "BRAIN_SUMMARY_BATCH_SIZE", 12))
SKILL_MAX_STEPS = max(1, min(5, int(getattr(config, "BRAIN_SKILL_MAX_STEPS", 4))))

_SUMMARY_LOCK = threading.Lock()
_SUMMARY_PENDING = {}
_SUMMARY_RUNNING = set()


def _extract_json(text: str):
    text = str(text or "").strip()
    if not text:
        return None

    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```$", "", text).strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None

    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _mem_key(user_id: str):
    uid = str(user_id or "default").strip() or "default"
    return f"brain_chat_memory:{uid}"


def _load_memory(user_id: str):
    mem = get_json(_mem_key(user_id))
    if isinstance(mem, dict):
        mem.setdefault("summary", "")
        mem.setdefault("messages", [])
        return mem
    return {"summary": "", "messages": []}


def _save_memory(user_id: str, mem: dict):
    set_json(_mem_key(user_id), mem, ex=CHAT_MEM_TTL)


def _fallback_summary(summary: str, messages):
    lines = []
    for m in messages[-8:]:
        role = str(m.get("role", "unknown"))
        content = str(m.get("content", "")).strip()
        if not content:
            continue
        lines.append(f"{role}: {content[:80]}")

    add = " | ".join(lines)
    if not add:
        return summary

    if summary:
        merged = f"{summary} || {add}"
    else:
        merged = add
    return merged[:1200]


def _summarize(summary: str, messages):
    if not messages:
        return summary

    history = "\n".join(
        f"{m.get('role', 'unknown')}: {str(m.get('content', '')).strip()}" for m in messages
    )
    prompt = f"""
你是会话摘要助手。请将新增对话压缩为简洁摘要，保留：
- 用户偏好与关注点
- 已确认的结论和约束
- 后续应保持的一致口径

已有摘要：
{summary}

新增对话：
{history}
"""
    try:
        result = str(_summary_llm.invoke(prompt).content).strip()
        return result or _fallback_summary(summary, messages)
    except Exception:
        return _fallback_summary(summary, messages)


def _summary_worker(user_id: str):
    while True:
        with _SUMMARY_LOCK:
            pending = _SUMMARY_PENDING.get(user_id, [])
            if not pending:
                _SUMMARY_PENDING.pop(user_id, None)
                _SUMMARY_RUNNING.discard(user_id)
                return
            batch = pending[:SUMMARY_BATCH_SIZE]
            del pending[: len(batch)]

        mem = _load_memory(user_id)
        old_summary = mem.get("summary", "")
        new_summary = _summarize(old_summary, batch)
        if new_summary != old_summary:
            mem["summary"] = new_summary
            _save_memory(user_id, mem)


def _enqueue_summary(user_id: str, old_messages):
    if not old_messages:
        return

    with _SUMMARY_LOCK:
        queue = _SUMMARY_PENDING.setdefault(user_id, [])
        queue.extend(old_messages)
        if user_id in _SUMMARY_RUNNING:
            return
        _SUMMARY_RUNNING.add(user_id)

    threading.Thread(target=_summary_worker, args=(user_id,), daemon=True).start()


def _record_chat_turn(user_id: str, user_text: str, assistant_text: str):
    mem = _load_memory(user_id)
    messages = mem.get("messages", [])

    if user_text:
        messages.append({"role": "user", "content": str(user_text).strip()})
    if assistant_text:
        messages.append({"role": "assistant", "content": str(assistant_text).strip()})

    overflow = []
    if len(messages) > CHAT_WINDOW_SIZE:
        overflow = messages[:-CHAT_WINDOW_SIZE]
        messages = messages[-CHAT_WINDOW_SIZE:]

    mem["messages"] = messages
    _save_memory(user_id, mem)
    _enqueue_summary(user_id, overflow)


def _rule_select(text: str, skills: dict):
    t = str(text or "")

    if re.search(r"(加入|添加|关注|删除|移除|取消关注|监控列表|自选|watchlist|list|add|remove)", t, re.IGNORECASE) and not re.search(r"(规则|monitor_rules)", t, re.IGNORECASE):
        if "watchlist_skill" in skills:
            return "watchlist_skill"

    if re.search(r"(当前价|现价|最新价|最新价格|股价|price|quote|last)", t, re.IGNORECASE) and not re.search(r"(新增规则|添加规则|配置规则|阈值|window|规则列表|查看规则|monitor_rules|当前规则)", t, re.IGNORECASE):
        if "current_price_skill" in skills:
            return "current_price_skill"

    if re.search(r"(分析|诊断|analyze)", t, re.IGNORECASE):
        if "analyze_stock_skill" in skills:
            return "analyze_stock_skill"

    return None


def _fallback_plan_action(text: str, skills: dict):
    """当路由 LLM 超时/失败时，对明显动作类请求做确定性兜底。"""
    t = str(text or "")

    if re.search(r"(规则列表|查看规则|monitor_rules|当前规则)", t, re.IGNORECASE):
        if "view_monitor_rules_skill" in skills:
            return {"mode": "skill", "skill": "view_monitor_rules_skill"}

    if re.search(r"(新增规则|添加规则|配置规则|删除规则|移除规则|阈值|window|秒|放量|涨跌幅)", t, re.IGNORECASE):
        if "config_monitor_skill" in skills:
            return {"mode": "skill", "skill": "config_monitor_skill"}

    if re.search(r"(加入|添加|关注|删除|移除|取消关注|监控列表|自选|watchlist|list|add|remove)", t, re.IGNORECASE) and not re.search(r"(规则|monitor_rules)", t, re.IGNORECASE):
        if "watchlist_skill" in skills:
            return {"mode": "skill", "skill": "watchlist_skill"}

    if re.search(r"(分析|诊断|analyze)", t, re.IGNORECASE):
        if "analyze_stock_skill" in skills:
            return {"mode": "skill", "skill": "analyze_stock_skill"}

    if re.search(r"(当前价|现价|最新价|最新价格|股价|price|quote|last)", t, re.IGNORECASE):
        if "current_price_skill" in skills:
            return {"mode": "skill", "skill": "current_price_skill"}

    return {"mode": "chat"}


def _plan_action(text: str, skills: dict, user_id: str):
    if not skills:
        return {"mode": "chat"}

    mem = _load_memory(user_id)
    summary = mem.get("summary", "")
    recent = mem.get("messages", [])[-CHAT_WINDOW_SIZE:]
    history = "\n".join(
        f"{m.get('role', 'unknown')}: {str(m.get('content', '')).strip()}" for m in recent
    )

    skill_lines = [f"- {name}: {meta['description']}" for name, meta in skills.items()]
    prompt = f"""
你是金融对话助手的大脑，需要决定：直接对话回答，还是调用某个skill执行。

可用skills：
{chr(10).join(skill_lines)}

决策规则：
1) 用户在聊市场、投资知识、策略思路、风险教育 -> mode=chat
2) 用户明确要求执行动作（如分析个股、监控列表增删查、查看规则、新增或修改盯盘规则）-> mode=skill
3) 能直接回答就不要调用skill

只输出JSON，不要额外文本。
格式：
{{"mode":"chat","answer":"..."}}
或
{{"mode":"skill","skill":"skill_name"}}

用户历史摘要：
{summary}

最近对话：
{history}

用户输入："{text}"
"""

    try:
        out = _router_llm.invoke(prompt).content
        data = _extract_json(out)
        if isinstance(data, dict):
            mode = str(data.get("mode", "")).strip().lower()
            if mode == "skill":
                name = str(data.get("skill", "")).strip()
                if name in skills:
                    return {"mode": "skill", "skill": name}
            if mode == "chat":
                answer = str(data.get("answer", "")).strip()
                return {"mode": "chat", "answer": answer}
    except Exception:
        pass

    return _fallback_plan_action(text, skills)

def _chat_reply(text: str, user_id: str):
    mem = _load_memory(user_id)
    summary = mem.get("summary", "")
    recent = mem.get("messages", [])[-CHAT_WINDOW_SIZE:]
    history = "\n".join(
        f"{m.get('role', 'unknown')}: {str(m.get('content', '')).strip()}" for m in recent
    )

    prompt = f"""
你是一个专业、稳健的金融对话助手。
请用中文回答，内容清晰、实用、避免夸大承诺，并提示关键风险。

用户历史摘要：
{summary}

最近对话：
{history}

当前用户输入：{text}
"""
    try:
        answer = str(_chat_llm.invoke(prompt).content).strip()
    except Exception as e:
        answer = f"服务暂时不可用: {e.__class__.__name__}"

    _record_chat_turn(user_id, text, answer)
    return answer


def _invoke_skill_once(skills: dict, skill_name: str, text: str, user_id: str, params: dict | None = None):
    """执行一次 skill 调用并返回统一结构，便于多步编排做错误处理。"""
    skill = skills.get(skill_name)
    if not skill:
        return {
            "ok": False,
            "skill": skill_name,
            "output": f"技能不存在: {skill_name}",
        }

    call_text = str(text or "").strip()
    call_ctx = {"user_id": user_id}
    if isinstance(params, dict):
        if params.get("text") is not None:
            call_text = str(params.get("text")).strip()
        call_ctx.update({k: v for k, v in params.items() if k != "text"})

    try:
        raw = skill["run"](call_text, call_ctx)
    except TypeError:
        try:
            raw = skill["run"](call_text)
        except Exception as e:
            return {
                "ok": False,
                "skill": skill_name,
                "output": f"Skill 调用失败: {e.__class__.__name__}",
            }
    except Exception as e:
        return {
            "ok": False,
            "skill": skill_name,
            "output": f"Skill 调用失败: {e.__class__.__name__}",
        }

    return {
        "ok": True,
        "skill": skill_name,
        "output": str(raw or "").strip(),
    }


def _decide_skill_action(
    text: str,
    summary: str,
    history: str,
    skills: dict,
    traces: list,
):
    """让 LLM 判断下一步：继续调 skill 或直接给最终回答。"""
    skill_lines = [f"- {name}: {meta['description']}" for name, meta in skills.items()]
    trace_lines = []
    for idx, item in enumerate(traces, 1):
        trace_lines.append(
            f"Step {idx} | skill={item.get('skill', '')} | output={str(item.get('output', ''))[:1200]}"
        )

    prompt = f"""
你是金融对话助手的大脑，采用 ReAct 风格做多步决策。
你可以基于当前信息继续调用 skill，或直接输出最终答案。

可用 skills：
{chr(10).join(skill_lines)}

用户历史摘要：
{summary}

最近对话：
{history}

用户当前问题：
{text}

已执行步骤：
{chr(10).join(trace_lines) if trace_lines else "(暂无)"}

决策要求：
1) 如果信息不足，请继续调用 skill。
2) 如果信息已经足够，请输出最终答案。
3) 严格只输出 JSON，不要任何额外文本。

输出二选一：
{{"action":"call_skill","skill":"skill_name","params":{{...}}}}
{{"action":"final_answer","answer":"最终回复内容"}}
"""

    try:
        data = _extract_json(_router_llm.invoke(prompt).content)
        if isinstance(data, dict):
            action = str(data.get("action", "")).strip().lower()
            if action == "call_skill":
                skill_name = str(data.get("skill", "")).strip()
                params = data.get("params", {})
                if skill_name in skills and isinstance(params, dict):
                    return {"action": "call_skill", "skill": skill_name, "params": params}
            if action == "final_answer":
                answer = str(data.get("answer", "")).strip()
                return {"action": "final_answer", "answer": answer}
    except Exception:
        pass

    return {"action": "final_answer", "answer": ""}


def _compose_final_answer(text: str, summary: str, history: str, traces: list):
    """在决策输出缺失或循环结束时，统一生成最终自然语言答复。"""
    trace_lines = []
    for idx, item in enumerate(traces, 1):
        trace_lines.append(
            f"Step {idx} | skill={item.get('skill', '')} | output={str(item.get('output', ''))[:1500]}"
        )

    prompt = f"""
你是一个专业、稳健的金融对话助手。
请根据多步 skill 执行结果，给出最终答复。

要求：
1) 用中文输出，结构清晰，先结论后依据。
2) 只依据已给出的 skill 结果，不要编造。
3) 保留关键风险提示，避免收益承诺。

用户历史摘要：
{summary}

最近对话：
{history}

用户当前问题：
{text}

多步技能结果：
{chr(10).join(trace_lines) if trace_lines else "(暂无)"}
"""

    try:
        answer = str(_chat_llm.invoke(prompt).content).strip()
    except Exception:
        answer = ""

    if not answer:
        fallback = str(traces[-1].get("output", "")).strip() if traces else ""
        return fallback or "服务暂时不可用: EmptyFinalAnswer"
    return answer


def _skill_reply(text: str, user_id: str, skills: dict, first_skill: str, first_output):
    """
    ReAct 风格多步 skill 编排：
    1) 先记录首个 skill 的输出作为已知事实。
    2) 每一步让 LLM 决定：继续调用 skill，还是直接给最终答案。
    3) 最大步数受限，避免死循环。
    4) 无论成功或异常，最终都写入会话记忆，保证后续可追溯。
    """
    mem = _load_memory(user_id)
    summary = mem.get("summary", "")
    recent = mem.get("messages", [])[-CHAT_WINDOW_SIZE:]
    history = "\n".join(
        f"{m.get('role', 'unknown')}: {str(m.get('content', '')).strip()}" for m in recent
    )

    traces = [{"skill": first_skill, "output": str(first_output or "").strip()}]

    for _ in range(SKILL_MAX_STEPS):
        decision = _decide_skill_action(text, summary, history, skills, traces)
        action = str(decision.get("action", "")).strip().lower()

        if action == "call_skill":
            next_skill = str(decision.get("skill", "")).strip()
            params = decision.get("params", {})
            invoked = _invoke_skill_once(skills, next_skill, text, user_id, params)
            traces.append(
                {
                    "skill": next_skill,
                    "output": str(invoked.get("output", "")).strip(),
                }
            )
            continue

        if action == "final_answer":
            answer = str(decision.get("answer", "")).strip()
            if not answer:
                answer = _compose_final_answer(text, summary, history, traces)
            _record_chat_turn(user_id, text, answer)
            return answer

    answer = _compose_final_answer(text, summary, history, traces)
    _record_chat_turn(user_id, text, answer)
    return answer


def _run_skill(skills: dict, skill_name: str, text: str, user_id: str):
    first = _invoke_skill_once(skills, skill_name, text, user_id, params=None)
    if not first.get("ok"):
        answer = str(first.get("output", "服务暂时不可用: SkillInvokeFailed")).strip()
        _record_chat_turn(user_id, text, answer)
        return answer

    raw_output = str(first.get("output", "")).strip()

    if skill_name in {"config_monitor_skill", "view_monitor_rules_skill"}:
        answer = raw_output or "服务暂时不可用: EmptySkillResult"
        _record_chat_turn(user_id, text, answer)
        return answer

    return _skill_reply(text, user_id, skills, skill_name, raw_output)

def handle_user_message(text: str, user_id: str = "default"):
    skills = _SKILL_MANAGER.load_skills()

    forced_skill = _rule_select(text, skills)
    if forced_skill:
        return _run_skill(skills, forced_skill, text, user_id)

    plan = _plan_action(text, skills, user_id)
    if plan.get("mode") == "skill":
        return _run_skill(skills, plan.get("skill", ""), text, user_id)

    answer = str(plan.get("answer", "")).strip()
    if answer:
        _record_chat_turn(user_id, text, answer)
        return answer

    return _chat_reply(text, user_id)


