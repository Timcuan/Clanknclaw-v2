import asyncio
import os
import httpx
import json

async def test_gemini():
    # Native .env loader
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k] = v.strip().strip("'").strip('"')

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY is NOT set in environment.")
        return

    print(f"📡 Testing Gemini with key starting with: {api_key[:10]}...")
    
    # 3-tier strategy mirrors llm.py v0.8.2 logic
    tiers = [
        "models/gemini-3.1-flash-lite-preview",
        "models/gemini-3-flash-preview",
        "models/gemini-3.1-pro-preview"
    ]
    
    for model_path in tiers:
        print(f"🔄 Trying model: {model_path}...")
        # URL construction matches llm.py
        url = f"https://generativelanguage.googleapis.com/v1beta/{model_path}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": "Suggest 1 creative token name and symbol. Return ONLY JSON: {\"name\": \"...\", \"symbol\": \"...\"}"}]}]
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload)
                print(f"📊 Status Code: {resp.status_code}")
                if resp.status_code == 200:
                    print(f"✅ Success: {resp.json()}")
                    return
                else:
                    print(f"❌ Error Body: {resp.text}")
        except Exception as e:
            print(f"💥 Exception: {e}")
            
    print("💀 ALL TIERS FAILED.")

if __name__ == "__main__":
    asyncio.run(test_gemini())
