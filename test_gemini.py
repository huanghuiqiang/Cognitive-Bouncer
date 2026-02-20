import os
import httpx
from dotenv import load_dotenv
from openai import OpenAI

def main():
    # 加载 .env 文件中的环境变量
    load_dotenv()
    
    # 代理设置
    proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    
    # OpenRouter API Key
    api_key = os.getenv("GEMINI_API_KEY") 
    
    if not api_key or "sk-or" not in api_key:
        print("❌ [错误]: 找不到有效的 OpenRouter 形式的 API Key。")
        return

    print("✅ 已成功读取 OpenRouter Key，正在连接 Gemini 2.0 Flash (via OpenRouter)...")
    
    try:
        # 如果有代理，配置 httpx client
        http_client = None
        if proxy:
            print(f"🌐 使用代理: {proxy}")
            http_client = httpx.Client(proxy=proxy)
        
        # 初始化 OpenAI 客户端
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            http_client=http_client
        )
        
        prompt = "用一句话解释：为什么对于程序员来说，'认知摩擦'是好东西？"
        print(f"👉 提问: {prompt}")
        print("等待响应中...\n")

        # 调用 Gemini 2.0 Flash 模型
        response = client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=[
                {"role": "user", "content": prompt}
            ],
            extra_headers={
                "HTTP-Referer": "https://github.com/Antigravity",
                "X-Title": "Antigravity Bouncer",
            }
        )
        
        print("🟢 [来自 OpenRouter/Gemini 的回复]:")
        print(response.choices[0].message.content)
        print("\n🎉 测试成功！OpenRouter 链路已通。")

    except Exception as e:
        print(f"\n❌ [调用失败]: {str(e)}")
        import traceback
        traceback.print_exc()
        print("请检查你的 API Key 或网络代理设置。")

if __name__ == "__main__":
    main()
