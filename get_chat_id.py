"""
get_chat_id.py
──────────────
首次使用工具：获取你的 Telegram Chat ID。

使用步骤：
  1. 先在 Telegram 里给你的 Bot 发一条消息（任何内容）
  2. 运行本脚本: python get_chat_id.py
  3. 将输出的 chat_id 填入 .env: TELEGRAM_CHAT_ID=xxx
"""

import json
import os
import requests

_OPENCLAW_CONFIG = os.path.expanduser("~/.openclaw/openclaw.json")

def get_token():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if token:
        return token
    with open(_OPENCLAW_CONFIG) as f:
        cfg = json.load(f)
    return cfg["channels"]["telegram"]["botToken"]

def main():
    token = get_token()
    resp = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        timeout=10
    ).json()

    results = resp.get("result", [])
    if not results:
        print("⚠️  未找到任何消息记录。")
        print("   请先在 Telegram 里给你的 Bot 发一条消息，然后重新运行本脚本。")
        return

    # 汇总所有不同的 chat
    chats = {}
    for update in results:
        msg = update.get("message") or update.get("channel_post")
        if msg and "chat" in msg:
            chat = msg["chat"]
            cid = chat["id"]
            name = chat.get("first_name", "") or chat.get("title", "")
            chats[cid] = {"id": cid, "name": name, "type": chat.get("type")}

    print("\n✅ 找到以下 Chat：\n")
    for c in chats.values():
        print(f"  📱 {c['name']} ({c['type']})")
        print(f"     Chat ID: {c['id']}")
        print()

    print("请将你的 Chat ID 添加到 .env 文件：")
    print("  TELEGRAM_CHAT_ID=<上面的数字ID>")

if __name__ == "__main__":
    main()
