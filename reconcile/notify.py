"""Alert transports. `send(severity, text, key, route)` returns a receipt string or raises.

Modes (`alerts.mode`):
  record  every message is written as one JSON line (to `alerts.record_path`, else stdout); nothing leaves the host
  live    route "telegram" -> Telegram bot, route "discord" -> Discord webhook (plan §6, D2)
          ("telegram", the pre-framework name, is accepted as an alias of "live")
Secrets are read from the environment variables the config names; development and QA name the
TEST_* variables. Error texts never contain a token or a webhook URL.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class RecordOnly:
    """Logs alerts as JSON lines; nothing leaves the host."""

    def __init__(self, path: str | None = None, clock=None):
        self.path, self.clock = path, clock

    def send(self, severity: str, text: str, key: str, route: str | None = None) -> str:
        line = json.dumps({"alert": key, "severity": severity, "route": route, "text": text})
        if self.path:
            with open(self.path, "a") as fh:
                fh.write(line + "\n")
        else:
            print(line, flush=True)
        return "recorded"


class Telegram:
    def __init__(self, token: str, chat_id: str, timeout: float = 15):
        self.token, self.chat_id, self.timeout = _clean_secret(token), _clean_secret(chat_id), timeout

    def send(self, severity: str, text: str, key: str, route: str | None = None) -> str:
        body = json.dumps({"chat_id": self.chat_id, "text": text[:4000], "disable_web_page_preview": True}).encode()
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/sendMessage", body,
                                         {"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                out = json.load(resp)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"telegram HTTP {exc.code}") from None
        except Exception as exc:  # noqa: BLE001 - messages may embed the URL, which holds the token
            raise RuntimeError(f"telegram delivery error: {type(exc).__name__}") from None
        if not out.get("ok"):
            raise RuntimeError(f"telegram rejected: {str(out.get('description', '?')).replace(self.token, '<token>')}")
        return f"telegram:{out['result']['message_id']}"


class Discord:
    """Discord webhook (the URL is the secret)."""

    def __init__(self, webhook: str, timeout: float = 15):
        self.webhook, self.timeout = _clean_secret(webhook), timeout

    def send(self, severity: str, text: str, key: str, route: str | None = None) -> str:
        body = json.dumps({"content": text[:1990], "allowed_mentions": {"parse": []}}).encode()
        try:
            req = urllib.request.Request(self.webhook, body,
                                         {"content-type": "application/json", "user-agent": "ssv-watch/1.0"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"discord HTTP {exc.code}") from None
        except Exception as exc:  # noqa: BLE001 - messages may embed the webhook URL
            raise RuntimeError(f"discord delivery error: {type(exc).__name__}") from None
        return f"discord:{status}"


class Router:
    """Live mode: one transport per route; a route without a transport is a delivery error."""

    def __init__(self, transports: dict):
        self.transports = transports

    def send(self, severity: str, text: str, key: str, route: str | None = None) -> str:
        transport = self.transports.get(route)
        if transport is None:
            raise RuntimeError(f"no transport configured for route {route!r}")
        return transport.send(severity, text, key, route)


def _clean_secret(value: str) -> str:
    return value.strip().strip("\"'").strip()


def _env(name: str | None) -> str:
    return _clean_secret(os.environ.get(name, "")) if name else ""


def make_notifier(alerts: dict):
    mode = alerts.get("mode", "record")
    if mode == "record":
        return RecordOnly(alerts.get("record_path"))
    if mode in ("live", "telegram"):
        token = _env(alerts.get("telegram_token_env", "TELEGRAM_TOKEN"))
        chat = _env(alerts.get("telegram_chat_id_env", "TELEGRAM_CHAT_ID"))
        hook = _env(alerts.get("discord_webhook_env", "DISCORD_WEBHOOK_INFO"))
        if not token or not chat:
            raise SystemExit("live alerts need the Telegram token and chat id environment variables set")
        transports = {"telegram": Telegram(token, chat)}
        if hook:
            transports["discord"] = Discord(hook)
        elif mode == "live":
            raise SystemExit("live alerts need the Discord webhook environment variable set")
        else:
            transports["discord"] = transports["telegram"]  # pre-framework single-channel deployments
        return Router(transports)
    raise SystemExit(f"unknown alerts.mode {mode!r}")
