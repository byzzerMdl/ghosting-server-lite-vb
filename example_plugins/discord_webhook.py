"""Announce join/leave events in a Discord channel via an incoming webhook.

This is a *Python* plugin. Unlike the sandboxed Lua plugins, Python plugins
have full stdlib access, so this one can reach the network and talk to
Discord's HTTP API.

Setup
-----
1. In Discord:  Server Settings -> Integrations -> Webhooks -> New Webhook,
   pick a channel, and copy the "Webhook URL".
2. Give the server the URL, either by:
     - setting an environment variable before launch:
           export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."   (bash)
           $env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."   (PowerShell)
     - or pasting it into WEBHOOK_URL below.
3. Copy this file into your --plugins directory and start the server:
       cp example_plugins/discord_webhook.py plugins/
       python -m ghostserver

If no URL is configured the plugin loads but stays silent.
"""

import json
import os
import threading
import urllib.request

# Paste your webhook URL here, or leave it and use the env var instead.
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# What Discord shows as the message author.
BOT_NAME = "Ghosling"


def _post(content: str):
    """Fire a webhook message on a background thread so a slow/failed HTTP
    request never stalls the 20 Hz server loop."""
    if not WEBHOOK_URL:
        return
    payload = json.dumps({"content": content, "username": BOT_NAME})
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    def send():
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as exc:  # network hiccup: log, don't crash
            server.log(f"discord_webhook: post failed: {exc}")

    threading.Thread(target=send, daemon=True).start()


def on_load():
    if WEBHOOK_URL:
        server.log("discord_webhook: ready")
        _post(":satellite: Ghost server online")
    else:
        server.log("discord_webhook: no webhook URL "
                   "(set DISCORD_WEBHOOK_URL or edit WEBHOOK_URL) — staying silent")


def on_connect(client):
    online = server.client_count()
    _post(f":green_circle: **{client['name']}** joined  ({online} online)")


def on_disconnect(client):
    # on_disconnect fires while the leaving client is still counted, so the
    # post-leave total is one fewer.
    online = max(0, server.client_count() - 1)
    _post(f":red_circle: **{client['name']}** left  ({online} online)")
