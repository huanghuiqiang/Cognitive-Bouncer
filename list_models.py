import os
from dotenv import load_dotenv
from google import genai

def main():
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if os.getenv("HTTP_PROXY"):
        os.environ["HTTP_PROXY"] = os.getenv("HTTP_PROXY")
    if os.getenv("HTTPS_PROXY"):
        os.environ["HTTPS_PROXY"] = os.getenv("HTTPS_PROXY")
        
    client = genai.Client(api_key=api_key)
    print("Listing available models...")
    for model in client.models.list():
        print(f"Model ID: {model.name}, Display Name: {model.display_name}")

if __name__ == "__main__":
    main()
