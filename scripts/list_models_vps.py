import asyncio
import os
from pathlib import Path

import httpx


def _load_env_if_present() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


async def _probe_generate_content(
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
) -> tuple[int, float]:
    url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": 'Return JSON only: {"ok": true}'}]}],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    response = await client.post(url, json=payload)
    return response.status_code, response.elapsed.total_seconds()


async def list_models() -> None:
    _load_env_if_present()
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        print("Error: GEMINI_API_KEY not found in .env")
        return

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            models = data.get("models", [])
            print(f"--- Found {len(models)} models ---")
            for model in models:
                name = model.get("name", "")
                display_name = model.get("displayName", "")
                print(f"ID: {name} | Name: {display_name}")

            print("\n--- Probe generateContent (24/7 candidates) ---")
            probe_candidates = [
                "models/gemini-2.5-flash-lite",
                "models/gemini-flash-lite-latest",
                "models/gemini-2.5-flash",
                "models/gemini-flash-latest",
            ]
            for model in probe_candidates:
                status, seconds = await _probe_generate_content(client, api_key, model)
                print(f"{model}: status={status} latency={seconds:.3f}s")
        except Exception as exc:
            print(f"Error fetching models: {exc}")


if __name__ == "__main__":
    asyncio.run(list_models())
