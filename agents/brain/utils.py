"""brain 共享工具函数。"""

import json
import re


def extract_json(text: str):
    """从 LLM 输出中提取 JSON，兼容 markdown 代码块包裹。"""
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
