from __future__ import annotations

import json
from typing import Any

import requests

from ..config import settings


class FeishuService:
    def __init__(self) -> None:
        self._tenant_access_token: str = ""

    def send_webhook_text(self, text: str) -> dict[str, Any]:
        if not settings.feishu_webhook_url:
            return {"ok": False, "message": "FEISHU_WEBHOOK_URL not configured"}

        payload = {"msg_type": "text", "content": {"text": text}}
        try:
            response = requests.post(settings.feishu_webhook_url, json=payload, timeout=15)
            response.raise_for_status()
            return {"ok": True, "message": "sent", "data": response.json()}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def get_tenant_access_token(self) -> str:
        if self._tenant_access_token:
            return self._tenant_access_token
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            return ""

        payload = {
            "app_id": settings.feishu_app_id,
            "app_secret": settings.feishu_app_secret,
        }
        try:
            response = requests.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json=payload,
                timeout=20,
            )
            response.raise_for_status()
            data = response.json()
            token = str(data.get("tenant_access_token", "")).strip()
            if token:
                self._tenant_access_token = token
            return token
        except Exception:
            return ""

    def send_app_text(self, receive_id: str, text: str, receive_id_type: str = "chat_id") -> dict[str, Any]:
        token = self.get_tenant_access_token()
        if not token:
            return {"ok": False, "message": "failed to get tenant_access_token"}

        payload = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        try:
            response = requests.post(
                f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=20,
            )
            response.raise_for_status()
            return {"ok": True, "message": "sent", "data": response.json()}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

