"""
Skill template.

Usage:
1) Copy this file to a new module, for example: my_new_skill.py
2) Update SKILL_NAME and SKILL_DESCRIPTION
3) Implement can_handle and run
4) Save file; brain agent will dynamically load it without service restart
"""

from llm import get_llm

SKILL_NAME = "template_skill"
SKILL_DESCRIPTION = "示例模板：根据用户输入执行自定义能力"

# Optional: use llm only when needed
llm = get_llm(temperature=0, timeout=20, max_retries=1)


def can_handle(text: str) -> bool:
    """
    Return True when this skill should handle the input.
    Keep this function lightweight and rule-based when possible.
    """
    t = str(text or "").strip()
    return t.startswith("模板:")


def run(text: str, context=None):
    """
    Required entry point.

    Args:
        text: user input text
        context: optional dict, may contain user_id and other metadata

    Returns:
        str or list[str]
    """
    context = context or {}
    user_id = context.get("user_id", "default")
    payload = str(text or "").replace("模板:", "", 1).strip()

    if not payload:
        return "模板 skill 已触发：请在“模板:”后输入内容。"

    # Example plain return without LLM
    return f"模板 skill 已处理，user_id={user_id}，内容={payload}"

    # Example LLM usage (uncomment if needed):
    # prompt = f"请处理以下内容：{payload}"
    # return str(llm.invoke(prompt).content).strip()
