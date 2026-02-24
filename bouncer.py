from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import feedparser  # type: ignore[import-untyped]
import httpx
import requests  # type: ignore[import-untyped]
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from agos.config import (
    bouncer_alert_suppress_minutes,
    bouncer_dedup_alert_threshold,
    bouncer_dedup_db_file,
    bouncer_feed_config_file,
    bouncer_lock_file,
    bouncer_state_file,
    inbox_path,
    min_score_threshold,
    model_bouncer,
    openrouter_api_key,
)
from agos.dedup_store import DedupMetrics, DedupStore
from agos.lock import LockAcquireError, file_lock
from agos.notify import send_bouncer_dedup_alert, send_bouncer_report

DB_FILE = bouncer_state_file()
DEDUP_DB_FILE = bouncer_dedup_db_file()
LOCK_FILE = bouncer_lock_file()
CONFIG_FILE = bouncer_feed_config_file()
MIN_SCORE_THRESHOLD = min_score_threshold()

_ALERT_SUPPRESS_KEY = "bouncer_dedup_alert_last_sent_at"


class ArticleEvaluation(BaseModel):
    score: float = Field(..., description="这篇内容的认知密度与价值得分 (0.0 - 10.0)")
    reason: str = Field(..., description="极简的一句话解释为什么给这个分数：是否有反共识或极高的技术价值")
    axiom_extracted: str = Field("", description="提取的核心公理(Axiom)：一句话总结其底层的规律或摩擦点。低分则填空字串。")


def load_processed() -> set[str]:
    if DB_FILE.exists():
        with DB_FILE.open("r", encoding="utf-8") as handle:
            return set(json.load(handle))
    return set()


def save_processed(processed_set: set[str]) -> None:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DB_FILE.open("w", encoding="utf-8") as handle:
        json.dump(sorted(processed_set), handle, ensure_ascii=False, indent=2)


def get_rss_urls() -> list[str]:
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
            return [str(u) for u in data.get("urls", [])]
    return ["https://news.ycombinator.com/rss"]


def fetch_content(url: str) -> str:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "html.parser")
                paragraphs = soup.find_all("p")
                text = " ".join(p.get_text() for p in paragraphs)
                return text[:5000]
    except Exception as exc:
        print(f"  [抓取正文失败 (跳过)]: {url} - {exc}")
    return ""


def evaluate_article(title: str, description: str, content: str) -> tuple[ArticleEvaluation, dict] | None:
    api_key = openrouter_api_key()
    if not api_key:
        print("  [配置缺失]: OPENROUTER_API_KEY / GEMINI_API_KEY 未设置")
        return None

    eval_text = f"Title: {title}\nSummary: {description}\nBody Snippet: {content[:1000]}"

    system_prompt = """
    你是一个名叫 'Antigravity Bouncer' 的认知守门员。你的唯一职责是对抗信息熵增。
    请阅读提交的文章摘要和片段，并评估其“认知摩擦（Friction）”和“系统2深度（System 2 Depth）”。

    【高分标准 (8.0-10.0)】：
    1. 具有强烈的“反共识”或颠覆传统的极客/工程视角。
    2. 能提炼出具有复利价值的“公理（Axiom）”或架构思想。
    3. 能够指导程序员去“造本能”，而不是“找轮子”。

    【低分垃圾 (0.0-7.9)：】
    1. 蹭热点的水文、情绪宣泄。
    2. 无脑搬运的新闻通稿、常识废话、“如何安装Python”等基础教程。
    3. 软广或标题党。

    请严格返回合法的 JSON 对象。包含以下字段：
    {"score": 数字(0-10), "reason": "极简的一句话解释是否有技术价值", "axiom_extracted": "提取的底层公理(低分可留空)"}
    确保除了上述 JSON 外不输出任何多余的 Markdown 标记或其他文本。
    """

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Antigravity",
            "X-Title": "Antigravity Bouncer",
        }

        payload = {
            "model": model_bouncer(),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": eval_text},
            ],
            "response_format": {"type": "json_object"},
        }

        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30.0,
        )

        if resp.status_code != 200:
            print(f"  [大模型响应异常]: HTTP {resp.status_code} - {resp.text}")
            return None

        resp_json = resp.json()
        usage = dict(resp_json.get("usage", {}))
        message_content = str(resp_json["choices"][0]["message"]["content"])
        json_str = message_content.strip().strip("```json").strip("```")

        evaluation = ArticleEvaluation.model_validate_json(json_str)
        return evaluation, usage

    except Exception as exc:
        print(f"  [大模型研判出错]: {exc}")
        return None


def export_to_inbox(title: str, url: str, score: float, reason: str, axiom: str) -> Path | None:
    safe_title = re.sub(r"[\\/*?:\"<>|]", "", title)[:60].strip()
    filename = f"Bouncer - {safe_title}.md"

    date_str = datetime.now().strftime("%Y-%m-%d")
    inbox_dir = inbox_path() / date_str
    inbox_dir.mkdir(parents=True, exist_ok=True)
    filepath = inbox_dir / filename

    content = f"""---
tags:
  - BouncerDump
score: {score}
status: pending
source: "{url}"
title: "{title.replace('"', "'")}"
created: "{date_str}"
---

# {title}

**来源链接**: [{url}]({url})
**认知得分**: {score}

> [!abstract] 核心公理 (Axiom)
> {axiom}

> [!info] 守门员判决理由 (Reason)
> {reason}
"""
    try:
        with filepath.open("w", encoding="utf-8") as handle:
            handle.write(content)
        print(f"  📥 [成功投递 Inbox]: {filename}")
        return filepath
    except Exception as exc:
        print(f"  ❌ [写入 Inbox 失败]: {exc}")
        return None


def _feed_metric_snapshot(feed_metrics: dict[str, dict[str, int]]) -> dict[str, DedupMetrics]:
    snap: dict[str, DedupMetrics] = {}
    for feed_url, data in feed_metrics.items():
        snap[feed_url] = DedupMetrics(
            fetched_count=data["fetched"],
            inserted_count=data["inserted"],
            deduped_count=data["deduped"],
        )
    return snap


def _should_send_dedup_alert(store: DedupStore, dedup_rate: float) -> bool:
    threshold = bouncer_dedup_alert_threshold()
    if dedup_rate < threshold:
        return False

    raw_last_sent = store.get_kv(_ALERT_SUPPRESS_KEY)
    if not raw_last_sent:
        return True

    try:
        last_sent = datetime.fromisoformat(raw_last_sent)
    except ValueError:
        return True

    gap_minutes = (datetime.now(UTC) - last_sent).total_seconds() / 60.0
    return gap_minutes >= bouncer_alert_suppress_minutes()


def _maybe_send_dedup_alert(
    store: DedupStore,
    dedup_rate: float,
    fetched_count: int,
    deduped_count: int,
    feed_metrics: dict[str, DedupMetrics],
) -> None:
    if not _should_send_dedup_alert(store, dedup_rate):
        return

    by_feed = []
    for feed_url, metrics in sorted(feed_metrics.items(), key=lambda x: x[0]):
        by_feed.append(
            f"- {feed_url}: {metrics.deduped_count}/{metrics.fetched_count} ({metrics.dedup_rate:.2f}%)"
        )

    trace_id = uuid4().hex[:12]
    ok = send_bouncer_dedup_alert(
        dedup_rate=dedup_rate,
        deduped_count=deduped_count,
        fetched_count=fetched_count,
        trace_id=trace_id,
        by_feed_lines=by_feed,
    )
    if ok:
        store.set_kv(_ALERT_SUPPRESS_KEY, datetime.now(UTC).isoformat())


def main() -> None:
    print("🚀 [Cognitive Bouncer] 引擎启动...")
    try:
        with file_lock(LOCK_FILE, timeout_sec=1.0):
            _run_once()
    except LockAcquireError as exc:
        print(f"⚠️ [Cognitive Bouncer] 已有实例运行中，跳过本次执行: {exc}")


def _run_once() -> None:
    legacy_processed = load_processed()
    rss_urls = get_rss_urls()
    dedup_store = DedupStore(DEDUP_DB_FILE)

    new_processed_this_run = 0
    golden_articles: list[dict[str, object]] = []
    total_tokens = 0
    dedup_reason_counts: dict[str, int] = defaultdict(int)
    feed_metrics_raw: dict[str, dict[str, int]] = defaultdict(lambda: {"fetched": 0, "inserted": 0, "deduped": 0})

    try:
        dedup_store.begin()
        if legacy_processed:
            imported = dedup_store.import_legacy_urls(legacy_processed)
            if imported:
                print(f"🔁 迁移旧去重缓存到 SQLite: {imported} 条")

        for feed_url in rss_urls:
            print(f"\n📡 正在接入数据源: {feed_url}")
            feed = feedparser.parse(feed_url)

            for entry in feed.entries[:5]:
                url = str(entry.get("link") or "")
                title = str(entry.get("title") or "Unknown Title")
                description = str(entry.get("description") or "")
                feed_metrics_raw[feed_url]["fetched"] += 1

                if not url:
                    continue

                check = dedup_store.check(url, title)
                if check.exists:
                    feed_metrics_raw[feed_url]["deduped"] += 1
                    dedup_reason_counts[check.reason] += 1
                    continue

                print(f"\n[检测到新文章]: {title}")
                blacklist = ["newsletter", "sponsored", "discount", "deal", "announcing", "hiring", "job", "offer"]
                if any(word in title.lower() for word in blacklist):
                    print("  🗑️ [本地拦截] 匹配黑名单关键词")
                    dedup_store.upsert_seen(url, title)
                    legacy_processed.add(url)
                    continue

                content_snippet = fetch_content(url)
                print("  🧠 提交给 Gemini 2.0 Flash 面试...")
                res = evaluate_article(title, description, content_snippet)

                note_path = ""
                if res:
                    evaluation, usage = res
                    total_tokens += int(usage.get("total_tokens", 0) or 0)
                    print(f"  📊 判决得分: {evaluation.score} (Usage: {usage.get('total_tokens')}t)")

                    if evaluation.score >= MIN_SCORE_THRESHOLD:
                        print(f"  🏆 [金子出现!] 提炼公理: {evaluation.axiom_extracted}")
                        written = export_to_inbox(title, url, evaluation.score, evaluation.reason, evaluation.axiom_extracted)
                        if written is not None:
                            note_path = str(written)
                            golden_articles.append(
                                {
                                    "title": title,
                                    "url": url,
                                    "score": evaluation.score,
                                    "axiom": evaluation.axiom_extracted,
                                }
                            )
                    else:
                        print("  🗑️ [抛弃垃圾]")

                dedup_store.upsert_seen(url, title, note_path=note_path)
                legacy_processed.add(url)
                feed_metrics_raw[feed_url]["inserted"] += 1
                new_processed_this_run += 1
                time.sleep(1.0)

        dedup_store.commit()
    except Exception:
        dedup_store.rollback()
        raise
    finally:
        dedup_store.close()

    save_processed(legacy_processed)

    print("\n" + "=" * 50)
    print(f"✅ 巡逻完成。共审查 {new_processed_this_run} 篇，消耗 {total_tokens} tokens。")
    print(f"👑 挖掘出的高认知密度文章: {len(golden_articles)} 篇。")
    print("=" * 50)

    for idx, art in enumerate(golden_articles, 1):
        print(f"\n💎 Top {idx}: 【{art['score']}分】 {art['title']}")
        print(f"🔗 链接: {art['url']}")
        print(f"🧠 核心公理: {art['axiom']}")

    print("\n📨 正在推送报告到 Telegram...")
    ok = send_bouncer_report(golden_articles, new_processed_this_run)
    if ok:
        print("✅ Telegram 推送成功")
    else:
        print("⚠️  Telegram 推送失败（请检查 .env 中的 TELEGRAM_CHAT_ID）")

    feed_metrics = _feed_metric_snapshot(feed_metrics_raw)
    all_metrics = DedupMetrics(
        fetched_count=sum(item.fetched_count for item in feed_metrics.values()),
        inserted_count=sum(item.inserted_count for item in feed_metrics.values()),
        deduped_count=sum(item.deduped_count for item in feed_metrics.values()),
    )

    print(
        "📈 去重统计: "
        f"fetched={all_metrics.fetched_count} inserted={all_metrics.inserted_count} "
        f"deduped={all_metrics.deduped_count} dedup_rate={all_metrics.dedup_rate:.2f}%"
    )
    if dedup_reason_counts:
        reason_text = ", ".join(f"{k}={v}" for k, v in sorted(dedup_reason_counts.items()))
        print(f"📌 去重原因分布: {reason_text}")

    dedup_store_alert = DedupStore(DEDUP_DB_FILE)
    try:
        _maybe_send_dedup_alert(
            dedup_store_alert,
            dedup_rate=all_metrics.dedup_rate,
            fetched_count=all_metrics.fetched_count,
            deduped_count=all_metrics.deduped_count,
            feed_metrics=feed_metrics,
        )
        dedup_store_alert.commit()
    finally:
        dedup_store_alert.close()


if __name__ == "__main__":
    main()
