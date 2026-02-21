"""
telegram_notify.py
──────────────────
Cognitive Bouncer 的 Telegram 推送模块。

直接调用 Telegram Bot API（sendMessage），无需 OpenClaw 中转。
Bot Token 从 OpenClaw 配置文件读取，Chat ID 从 .env 读取。

使用前准备：
  1. 在 Telegram 里给 Bot 发一条消息（任意内容即可）
  2. 运行 `python get_chat_id.py` 拿到你的 chat_id
  3. 把 chat_id 写入 .env: TELEGRAM_CHAT_ID=你的ID
"""

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

# ── 配置读取 ──────────────────────────────────────────────
_OPENCLAW_CONFIG = os.path.expanduser("~/.openclaw/openclaw.json")

def _get_bot_token() -> str:
    """优先从 .env 读，其次从 OpenClaw 配置读取 bot token。"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if token:
        return token
    try:
        with open(_OPENCLAW_CONFIG, "r") as f:
            cfg = json.load(f)
        token = cfg.get("channels", {}).get("telegram", {}).get("botToken", "")
        if token:
            return token
    except Exception:
        pass
    raise ValueError(
        "未找到 Telegram Bot Token。\n"
        "请在 .env 中设置 TELEGRAM_BOT_TOKEN=xxx\n"
        "或确保 ~/.openclaw/openclaw.json 中已配置 channels.telegram.botToken"
    )

def _get_chat_id() -> str:
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not chat_id:
        raise ValueError(
            "未找到 TELEGRAM_CHAT_ID。\n"
            "请先运行 `python get_chat_id.py` 获取你的 Chat ID，\n"
            "然后在 .env 中添加：TELEGRAM_CHAT_ID=你的ID"
        )
    return chat_id

# ── 核心发送函数 ──────────────────────────────────────────

def send_message(text: str, chat_id: str = None, parse_mode: str = "HTML") -> bool:
    """
    发送消息到 Telegram。

    Args:
        text:       消息内容（支持 HTML 标签，如 <b>粗体</b>、<code>代码</code>）
        chat_id:    目标 Chat ID（不传则从 .env 读取）
        parse_mode: "HTML" 或 "MarkdownV2"

    Returns:
        True = 发送成功，False = 失败
    """
    try:
        token = _get_bot_token()
        cid   = chat_id or _get_chat_id()
        url   = f"https://api.telegram.org/bot{token}/sendMessage"

        resp = requests.post(url, json={
            "chat_id":    cid,
            "text":       text,
            "parse_mode": parse_mode,
        }, timeout=15)

        if resp.status_code == 200 and resp.json().get("ok"):
            return True
        else:
            print(f"  ❌ [Telegram 发送失败] HTTP {resp.status_code}: {resp.text}")
            return False
    except ValueError as e:
        print(f"  ⚠️  [配置错误]: {e}")
        return False
    except Exception as e:
        print(f"  ❌ [Telegram 异常]: {str(e)}")
        return False


def send_bouncer_report(golden_articles: list, total_scanned: int) -> bool:
    """
    发送 Cognitive Bouncer 的完整巡逻报告到 Telegram。

    Args:
        golden_articles: 高分文章列表，每项含 title/url/score/axiom
        total_scanned:   本次扫描的文章总数

    Returns:
        True = 发送成功
    """
    if not golden_articles:
        text = (
            "🤖 <b>Cognitive Bouncer 巡逻完毕</b>\n\n"
            f"📊 共扫描 <b>{total_scanned}</b> 篇文章\n"
            "🗑️ 无高密度内容，全部过滤。"
        )
        return send_message(text)

    lines = [
        "🤖 <b>Cognitive Bouncer 报告</b>",
        f"📊 扫描 <b>{total_scanned}</b> 篇 → 挖出 <b>{len(golden_articles)}</b> 颗金子\n",
    ]

    for idx, art in enumerate(golden_articles, 1):
        score = art.get("score", 0)
        title = art.get("title", "Unknown")
        url   = art.get("url", "")
        axiom = art.get("axiom", "")

        # 评分 emoji
        if score >= 9.5:
            medal = "💎"
        elif score >= 9.0:
            medal = "🏆"
        elif score >= 8.5:
            medal = "🥇"
        else:
            medal = "⭐️"

        lines.append(f"{medal} <b>Top {idx}</b> [{score:.1f}分]")
        lines.append(f"📰 <a href=\"{url}\">{title}</a>")
        if axiom:
            lines.append(f"🧠 <i>{axiom}</i>")
        lines.append("")  # 空行分隔

    return send_message("\n".join(lines))
