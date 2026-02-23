import json
import time
import httpx
import feedparser
from bs4 import BeautifulSoup
from pathlib import Path
from datetime import datetime
from pydantic import BaseModel, Field
from agos.config import (
    bouncer_state_file,
    bouncer_feed_config_file,
    inbox_path,
    min_score_threshold,
    model_bouncer,
    openrouter_api_key,
)
from agos.notify import send_bouncer_report

# --- 配置与缓存 ---
DB_FILE = bouncer_state_file()
CONFIG_FILE = bouncer_feed_config_file()
MIN_SCORE_THRESHOLD = min_score_threshold()

# 引入 Pydantic 约束 LLM 输出格式（避免 JSON 解析错误）
class ArticleEvaluation(BaseModel):
    score: float = Field(..., description="这篇内容的认知密度与价值得分 (0.0 - 10.0)")
    reason: str = Field(..., description="极简的一句话解释为什么给这个分数：是否有反共识或极高的技术价值")
    axiom_extracted: str = Field("", description="提取的核心公理(Axiom)：一句话总结其底层的规律或摩擦点。低分则填空字串。")

def load_processed():
    if DB_FILE.exists():
        with DB_FILE.open("r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_processed(processed_set):
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DB_FILE.open("w", encoding="utf-8") as f:
        json.dump(sorted(processed_set), f, ensure_ascii=False, indent=2)

def get_rss_urls():
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("urls", [])
    return ["https://news.ycombinator.com/rss"]

# --- 核心网络与解析 ---

def fetch_content(url):
    """尝试抓取正文，如果失败则返回标题或为空。此步骤使用普通 requests，避免触发 OpenClaw"""
    try:
        # 使用简单的 Headers 绕过普通拦截
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        # 为了稳定，设置短超时时间
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "html.parser")
                # 简单提取段落
                paragraphs = soup.find_all('p')
                text = " ".join([p.get_text() for p in paragraphs])
                # 限制字数，防止污染上下文和过度消耗 Token
                return text[:5000]
    except Exception as e:
        print(f"  [抓取正文失败 (跳过)]: {url} - {str(e)}")
    return ""

import requests

def evaluate_article(title: str, description: str, content: str):
    """核心研判：基于 Antigravity 公理"""
    api_key = openrouter_api_key()
    if not api_key:
        print("  [配置缺失]: OPENROUTER_API_KEY / GEMINI_API_KEY 未设置")
        return None
    
    # 组合待评测文本
    eval_text = f"Title: {title}\nSummary: {description}\nBody Snippet: {content[:1000]}"
    
    system_prompt = """
    你是一个名叫 'Antigravity Bouncer' 的认知守门员。你的唯一职责是对抗信息熵增。
    请阅读提交的文章摘要和片段，并评估其“认知摩擦（Friction）”和“系统2深度（System 2 Depth）”。
    
    【高分标准 (8.0-10.0)】：
    1. 具有强烈的“反共识”或颠覆传统的极客/工程视角。
    2. 能提炼出具有复利价值的“公理（Axiom）”或架构思想。
    3. 能够指导程序员去“造本能”，而不是“找轮子”。
    
    【低分垃圾 (0.0-7.9)】：
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
            "X-Title": "Antigravity Bouncer"
        }
        
        payload = {
            "model": model_bouncer(),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": eval_text}
            ],
            "response_format": {"type": "json_object"}
        }
        
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30.0
        )
        
        if resp.status_code != 200:
            print(f"  [大模型响应异常]: HTTP {resp.status_code} - {resp.text}")
            return None
            
        resp_json = resp.json()
        usage = resp_json.get("usage", {})
        message_content = resp_json["choices"][0]["message"]["content"]
            
        json_str = message_content.strip().strip("```json").strip("```")
        evaluation = ArticleEvaluation.model_validate_json(json_str)
        return evaluation, usage
        
    except Exception as e:
        print(f"  [大模型研判出错]: {str(e)}")
        return None

import re

# --- 抛出端 (Delivery) ---

def export_to_inbox(title: str, url: str, score: float, reason: str, axiom: str):
    """将挖掘到的高密度金矿写入 Obsidian 00_Inbox 待审查，按日期分类"""
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title)[:60].strip()
    filename = f"Bouncer - {safe_title}.md"
    
    # 建立日期文件夹：00_Inbox/2026-02-23/
    date_str = datetime.now().strftime('%Y-%m-%d')
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
        with filepath.open("w", encoding="utf-8") as f:
            f.write(content)
        print(f"  📥 [成功投递 Inbox]: {filename}")
    except Exception as e:
        print(f"  ❌ [写入 Inbox 失败]: {str(e)}")


# --- 主流水线 ---

def main():
    print("🚀 [Cognitive Bouncer] 引擎启动...")
    processed_urls = load_processed()
    rss_urls = get_rss_urls()
    
    new_processed_this_run = 0
    golden_articles = []
    total_tokens = 0
    
    for feed_url in rss_urls:
        print(f"\n📡 正在接入数据源: {feed_url}")
        feed = feedparser.parse(feed_url)
        
        # 只取前 5 篇进行演示，避免一次性消耗过大
        for entry in feed.entries[:5]: 
            url = entry.get('link')
            title = entry.get('title', 'Unknown Title')
            
            if not url or url in processed_urls:
                continue
                
            print(f"\n[检测到新文章]: {title}")
            description = entry.get('description', '')
            
            # L1 本地过滤：基于关键词（省 Token）
            blacklist = ["newsletter", "sponsored", "discount", "deal", "announcing", "hiring", "job", "offer"]
            if any(word in title.lower() for word in blacklist):
                print(f"  🗑️ [本地拦截] 匹配黑名单关键词")
                processed_urls.add(url)
                continue
            
            # 获取部分正文供模型判断（L2 过滤准备）
            content_snippet = fetch_content(url)
            
            # L2 调用大模型研判
            print(f"  🧠 提交给 Gemini 2.0 Flash 面试...")
            res = evaluate_article(title, description, content_snippet)
            
            if res:
                evaluation, usage = res
                total_tokens += usage.get("total_tokens", 0)
                print(f"  📊 判决得分: {evaluation.score} (Usage: {usage.get('total_tokens')}t)")
                
                # 达到阈值，判定为金子
                if evaluation.score >= MIN_SCORE_THRESHOLD:
                    print(f"  🏆 [金子出现!] 提炼公理: {evaluation.axiom_extracted}")
                    export_to_inbox(title, url, evaluation.score, evaluation.reason, evaluation.axiom_extracted)
                    golden_articles.append({
                        "title": title,
                        "url": url,
                        "score": evaluation.score,
                        "axiom": evaluation.axiom_extracted
                    })
                else:
                    print("  🗑️ [抛弃垃圾]")
            
            # 无论高低分，都记录下来，绝不分析第二遍
            processed_urls.add(url)
            new_processed_this_run += 1
            
            # API 限流保护
            time.sleep(1.0) 
            
    # 持久化记忆
    save_processed(processed_urls)
    
    # 构建最终输出
    print("\n" + "="*50)
    print(f"✅ 巡逻完成。共审查 {new_processed_this_run} 篇，消耗 {total_tokens} tokens。")
    print(f"👑 挖掘出的高认知密度文章: {len(golden_articles)} 篇。")
    print("="*50)
    
    for idx, art in enumerate(golden_articles, 1):
        print(f"\n💎 Top {idx}: 【{art['score']}分】 {art['title']}")
        print(f"🔗 链接: {art['url']}")
        print(f"🧠 核心公理: {art['axiom']}")

    # ── Telegram 推送 ─────────────────────────────────────
    print("\n📨 正在推送报告到 Telegram...")
    ok = send_bouncer_report(golden_articles, new_processed_this_run)
    if ok:
        print("✅ Telegram 推送成功")
    else:
        print("⚠️  Telegram 推送失败（请检查 .env 中的 TELEGRAM_CHAT_ID）")

if __name__ == "__main__":
    main()
