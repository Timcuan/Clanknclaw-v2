import asyncio
import os
import json
import logging
from clankandclaw.utils.llm import enrich_signal_with_llm

# Manual log setup for test clarity
logging.basicConfig(level=logging.INFO)

async def test_enrichment():
    samples = [
        {
            "label": "Clanker Request",
            "text": "@clanker deploy $TIMC 'Timc the Cat' with image https://pbs.twimg.com/media/dummy.jpg"
        },
        {
            "label": "High-Alpha Context",
            "text": "This new dog token from the original Farcaster devs is actually insane. Ticker is $WOOF. No clanker mention yet but we should watch."
        }
    ]
    
    print("\n--- AI ENRICHMENT TEST (Zero-Dep) ---\n")
    
    for sample in samples:
        print(f"Testing: {sample['label']}")
        print(f"Text: {sample['text']}\n")
        
        result = await enrich_signal_with_llm(sample['text'])
        
        if result:
            print(f"  [√] AI Name: {result.get('name')}")
            print(f"  [√] AI Symbol: {result.get('symbol')}")
            print(f"  [√] AI Bullishness: {result.get('bullish_score')}%")
            print(f"  [√] AI Rationale: {result.get('ai_rationale')}")
        else:
            print("  [X] Failed to get enrichment (Check GEMINI_API_KEY).")
        print("-" * 30 + "\n")

if __name__ == "__main__":
    async def main():
        # Look for the .env manually if it exists to avoid dependency
        try:
            if os.path.exists(".env"):
                with open(".env") as f:
                    for line in f:
                        if "=" in line:
                            k, v = line.strip().split("=", 1)
                            os.environ[k] = v.strip("'").strip('"')
        except Exception:
            pass

        if not os.getenv("GEMINI_API_KEY"):
            print("GEMINI_API_KEY (AIzaSy...) not found.")
            return
            
        await test_enrichment()
    asyncio.run(main())
