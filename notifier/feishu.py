import json
from typing import Optional

import requests
import config


FEISHU_BASE = "https://open.feishu.cn"


def send(msg: str) -> bool:
    """通过群机器人Webhook发送文本。"""
    webhook = getattr(config, "FEISHU_WEBHOOK", "")
    if not webhook:
        return False

    try:
        resp = requests.post(
            webhook,
            json={
                "msg_type": "text",
                "content": {"text": msg},
            },
            timeout=8,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


def _get_tenant_access_token() -> Optional[str]:
    app_id = getattr(config, "FEISHU_APP_ID", "")
    app_secret = getattr(config, "FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        return None

    try:
        resp = requests.post(
            f"{FEISHU_BASE}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=8,
        )
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    if data.get("code") != 0:
        return None
    return data.get("tenant_access_token")


def reply_message(message_id: str, msg: str) -> bool:
    """
    优先按message_id原会话回复；若未配置应用凭证，则回退到Webhook群发。
    """
    token = _get_tenant_access_token()
    if token and message_id:
        try:
            resp = requests.post(
                f"{FEISHU_BASE}/open-apis/im/v1/messages/{message_id}/reply",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "msg_type": "text",
                    "content": json.dumps({"text": msg}, ensure_ascii=False),
                },
                timeout=8,
            )
            data = resp.json()
            if resp.status_code == 200 and data.get("code") == 0:
                return True
        except (requests.RequestException, ValueError):
            pass

    return send(msg)
