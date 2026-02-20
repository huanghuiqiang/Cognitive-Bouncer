import os
from dotenv import load_dotenv
from google import genai
import time

def main():
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if os.getenv("HTTP_PROXY"):
        os.environ["HTTP_PROXY"] = os.getenv("HTTP_PROXY")
    if os.getenv("HTTPS_PROXY"):
        os.environ["HTTPS_PROXY"] = os.getenv("HTTPS_PROXY")
        
    client = genai.Client(api_key=api_key)
    
    models_to_try = ['gemini-2.0-flash', 'gemini-1.5-flash', 'gemini-1.5-pro']
    
    for model_name in models_to_try:
        print(f"\n--- Testing model: {model_name} ---")
        try:
            response = client.models.generate_content(
                model=model_name,
                contents="Hello, testing."
            )
            print(f"✅ Success with {model_name}!")
            print(f"Response: {response.text}")
            break
        except Exception as e:
            print(f"❌ Failed with {model_name}: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()
