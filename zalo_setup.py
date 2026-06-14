"""Register / inspect the Zalo Bot webhook (bot.zaloplatforms.com).

Reads the bot token + webhook secret from app/credentials/zalo.credentials.json
or env (ZALO_BOT_TOKEN / ZALO_WEBHOOK_SECRET), the same resolution the server uses.

Usage:
    python zalo_setup.py me                       # verify the token (getMe)
    python zalo_setup.py set --url https://<endpoint>/zalo/webhook
    python zalo_setup.py info                      # getWebhookInfo
    python zalo_setup.py delete                    # deleteWebhook

The secret_token passed to Zalo MUST match ZALO_WEBHOOK_SECRET on the deployed
runtime — that is how the server verifies incoming pushes. If no secret is set,
this script generates one and prints it so you can save it in both places.
"""
from __future__ import annotations

import argparse
import secrets
import sys

from dotenv import load_dotenv

from app.credentials import load_credentials
from app.infrastructure.zalo_bot_client import ZaloBotClient, ZaloBotError


def _creds() -> tuple[str, str | None]:
    c = load_credentials("zalo", {"bot_token": "ZALO_BOT_TOKEN", "webhook_secret": "ZALO_WEBHOOK_SECRET"})
    token = c["bot_token"]
    if not token:
        sys.exit("ZALO_BOT_TOKEN not set (env or app/credentials/zalo.credentials.json).")
    return token, c["webhook_secret"]


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Zalo Bot webhook setup.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("me", help="getMe — verify the bot token")
    p_set = sub.add_parser("set", help="setWebhook")
    p_set.add_argument("--url", required=True, help="HTTPS webhook URL, e.g. https://<endpoint>/zalo/webhook")
    sub.add_parser("info", help="getWebhookInfo")
    sub.add_parser("delete", help="deleteWebhook")
    args = parser.parse_args()

    token, secret = _creds()
    client = ZaloBotClient(token)

    try:
        if args.cmd == "me":
            print(client.get_me())
        elif args.cmd == "info":
            print(client.get_webhook_info())
        elif args.cmd == "delete":
            print(client.delete_webhook())
        elif args.cmd == "set":
            if not secret:
                secret = secrets.token_urlsafe(24)
                print(f"[generated webhook secret — save this as ZALO_WEBHOOK_SECRET on the runtime AND locally]\n  {secret}\n")
            result = client.set_webhook(args.url, secret)
            print(f"webhook set → {args.url}\n{result}")
    except ZaloBotError as e:
        sys.exit(f"[error] {e}")


if __name__ == "__main__":
    main()
