import asyncio
import os
import httpx
import json

async def list_models():
    # Native .env loader
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k] = v.strip().strip("'").strip('"')

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY is NOT set.")
        return

    print(f"📡 Listing available models for key: {api_key[:10]}...")
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                print(f"❌ Error: {resp.status_code} - {resp.text}")
                return
            
            data = resp.json()
            print("✅ Available Models:")
            for m in data.get("models", []):
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    print(f"- {m['name']} ({m.get('description', '')[:50]})")
    except Exception as e:
        print(f"💥 Exception: {e}")

if __name__ == "__main__":
    asyncio.run(list_models())
