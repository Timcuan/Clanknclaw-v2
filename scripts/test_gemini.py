import asyncio
import os
from clankandclaw.utils.llm import extract_token_identity_with_llm

async def test_extraction():
    test_cases = [
        "deploy the new Timc the Cat coin [TIMC] right now!",
        "just launched a token called Super Nova. ticker: NOVA",
        "making a coin for my friend. Name: FriendToken - FRND",
        "Ambiguous post about nothing specific."
    ]
    
    print("━━━ GEMINI EXTRACTION TEST ━━━")
    for text in test_cases:
        try:
            name, symbol = await extract_token_identity_with_llm(text)
            print(f"Input: {text}")
            print(f"Result: Name='{name}', Symbol='{symbol}'")
            print("---")
        except Exception as e:
            print(f"Error testing '{text}': {e}")

if __name__ == "__main__":
    # Ensure environment is loaded or set manually for test
    if not os.getenv("GEMINI_API_KEY"):
        # Temporary load for test if needed, but .env should be there
        from pathlib import Path
        env_path = Path(".env")
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("GEMINI_API_KEY="):
                    os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1]
    
    asyncio.run(test_extraction())
