from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from stock_system.container import container  # noqa: E402


def print_json(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Feishu integration for the stock system.")
    parser.add_argument("--webhook", action="store_true", help="Send a webhook text message.")
    parser.add_argument("--tenant-token", action="store_true", help="Fetch tenant access token only.")
    parser.add_argument("--app-send", action="store_true", help="Send an app text message.")
    parser.add_argument("--text", default="股票系统飞书测试", help="Message text.")
    parser.add_argument("--receive-id", default="", help="Feishu receive_id for app messaging.")
    parser.add_argument(
        "--receive-id-type",
        default="chat_id",
        choices=["chat_id", "open_id", "user_id", "union_id", "email"],
        help="Feishu receive_id_type.",
    )
    args = parser.parse_args()

    if args.webhook:
        result = container.feishu.send_webhook_text(args.text)
        print_json(result)
        return 0 if result.get("ok") else 1

    if args.tenant_token:
        token = container.feishu.get_tenant_access_token()
        result = {
            "ok": bool(token),
            "message": "tenant_access_token ready" if token else "failed to get tenant_access_token",
        }
        print_json(result)
        return 0 if token else 1

    if args.app_send:
        if not args.receive_id:
            print_json({"ok": False, "message": "--receive-id is required for --app-send"})
            return 1
        result = container.feishu.send_app_text(
            receive_id=args.receive_id,
            text=args.text,
            receive_id_type=args.receive_id_type,
        )
        print_json(result)
        return 0 if result.get("ok") else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
