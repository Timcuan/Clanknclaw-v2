import asyncio
import inspect
import json
import logging
import os
import re
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias

import httpx

from clankandclaw.utils.limiter import gemini_limiter

logger = logging.getLogger(__name__)


TokenIdentity: TypeAlias = tuple[str, str]
TokenIdentityFallback: TypeAlias = Callable[[str], TokenIdentity | Awaitable[TokenIdentity]]


def _clean_best_effort(val: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ]+", "", val).strip()


class CircuitBreaker:
    """Manages AI API health to prevent redundant failing calls."""
    def __init__(self, failure_threshold: int = 3, cooldown_seconds: int = 120):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.failures = 0
        self.last_failure_time = 0.0
        self._lock = threading.Lock()

    def record_failure(self):
        with self._lock:
            self.failures += 1
            self.last_failure_time = time.monotonic()
            if self.failures >= self.failure_threshold:
                 logger.warning(f"AI Circuit Breaker triggered. Cooldown for {self.cooldown_seconds}s.")

    def record_success(self):
        with self._lock:
            self.failures = 0

    def is_available(self) -> bool:
        with self._lock:
            if self.failures < self.failure_threshold:
                return True
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed >= self.cooldown_seconds:
                self.failures = 0 # Reset after cooldown
                return True
            return False

# Global Circuit Breakers
gemini_breaker = CircuitBreaker()


async def extract_token_identity_with_llm(text: str) -> TokenIdentity:
    """Tiered extraction: Flash -> Pro -> Heuristic fallback."""
    if not gemini_breaker.is_available():
         return await _extract_heuristic(text)
         
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return await _extract_heuristic(text)

    prompt = f"Extract 'name' and 'symbol' (ticker) from this text. Return JSON only: {text}"
    
    for model in ["gemini-1.5-flash", "gemini-1.5-flash-8b"]:
        try:
            await gemini_limiter.wait()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}]
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 429: continue
                resp.raise_for_status()
                data = resp.json()
                content = data["candidates"][0]["content"]["parts"][0]["text"]
                gemini_breaker.record_success()
                obj = json.loads(content)
                return _clean_best_effort(obj.get("name", "Unknown"))[:50], _clean_best_effort(obj.get("symbol", "TKN"))[:10]
        except Exception:
            continue
            
    gemini_breaker.record_failure()
    return await _extract_heuristic(text)


async def enrich_signal_with_llm(text: str) -> dict[str, Any]:
    """
    Unified call for maximum efficiency with multi-tier fallback.
    Tiers: Flash -> Pro -> Heuristic.
    """
    if not gemini_breaker.is_available():
         return {}
         
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or api_key.startswith("YOUR_"):
        return {}
        
    prompt = f"""
    Analyze this social media post and return a JSON object with the following fields:
    - name: The official token name (null if not found).
    - symbol: The official token symbol/ticker (null if not found).
    - is_genuine_launch: Boolean. Is the user actually trying to deploy or requesting a launch?
    - bullish_score: Integer (1-100).
    - ai_rationale: One-sentence explanation.
    - suggested_description: A professional 150-200 char description.

    Text: {text}
    """
    
    # Tiered execution strategy
    for model in ["gemini-1.5-flash", "gemini-1.5-flash-8b"]:
        try:
            await gemini_limiter.wait()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}]
            }
            
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 429: # Rate Limit
                     logger.warning(f"Gemini {model} Rate Limited (429). Trying next tier...")
                     continue
                resp.raise_for_status()
                data = resp.json()
                content = data["candidates"][0]["content"]["parts"][0]["text"]
                
                gemini_breaker.record_success()
                result = json.loads(content)
                result["ai_model"] = model
                return result
                
        except Exception as exc:
            logger.warning(f"Gemini {model} failed: {exc}")
            continue

    # Final logic: record a failure and let the caller potentially use heuristics
    gemini_breaker.record_failure()
    return {}


async def _extract_heuristic(text: str) -> TokenIdentity:
    """
    Robust Heuristic Fallback Engine (Non-LLM).
    Used if standard regex fails or LLM is unavailable.
    """
    words = text.split()
    if not words:
        return "Unknown", "TKN"
        
    # Heuristic 1: Look for ALL-CAPS words of length 3-8 (Ticker candidates)
    tickers = [w.strip("$!?,.") for w in words if w.isupper() and 3 <= len(w.strip("$!?,.")) <= 8]
    symbol = tickers[-1] if tickers else "TKN"
    
    # Heuristic 2: Look for the first 2-3 words that look like a Name
    ignore = {"deploy", "launch", "make", "create", "token", "contract", "ticker", "symbol"}
    name_parts = []
    for w in words:
        clean_w = w.lower().strip("$!?,.")
        if clean_w in ignore or len(clean_w) < 2:
            continue
        name_parts.append(w)
        if len(name_parts) >= 3:
            break
            
    name = " ".join(name_parts) if name_parts else symbol
    return _clean_best_effort(name)[:50] or "Unknown", _clean_best_effort(symbol)[:10] or "TKN"


def call_token_identity_fallback(
    text: str,
    extractor: TokenIdentityFallback | None = None,
) -> TokenIdentity:
    fallback = extractor or extract_token_identity_with_llm
    result = fallback(text)
    if inspect.isawaitable(result):
        return _run_awaitable(result)
    return result


def _run_awaitable(awaitable: Awaitable[TokenIdentity]) -> TokenIdentity:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result: dict[str, TokenIdentity] = {}
    error: dict[str, BaseException] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(awaitable)
        except BaseException as exc:  # pragma: no cover - passthrough path
            error["value"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()

    if "value" in error:
        raise error["value"]
    return result["value"]


async def suggest_token_metadata(theme: str) -> list[dict[str, str]]:
    """Suggest creative and themed name/ticker pairs with fallback resilience."""
    if not gemini_breaker.is_available():
         return []
         
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return []

    prompt = f"""
    Suggest 4 creative, trending, and organic-looking meme token ideas for the Base Network.
    Theme/Prompt: '{theme}'
    
    IMPORTANT: Avoid generic 'AI-sounding' names (e.g., 'MemeDog', 'BaseMission').
    INSTEAD: Use high-conviction, natural, and low-cap tickers that 'snipers' hunt (e.g., 'PONKE', 'GIGA', 'HIM', 'PEPE').
    
    Each idea must have:
    - name: A catchy and natural name (max 30 chars).
    - symbol: A high-alpha ticker (3-6 chars, ALL CAPS).
    
    Return ONLY a JSON array of objects with 'name' and 'symbol' keys.
    """
    
    for model in ["gemini-1.5-flash", "gemini-1.5-flash-8b"]:
        try:
            await gemini_limiter.wait()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}]
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 403:
                     logger.error(f"Gemini {model} FORBIDDEN (403). CHECK API KEY.")
                     gemini_breaker.record_failure()
                     return []
                if resp.status_code == 400:
                     err_body = resp.text
                     logger.error(f"Gemini {model} 400 BAD REQUEST: {err_body}")
                     continue
                resp.raise_for_status()
                data = resp.json()
                content = data["candidates"][0]["content"]["parts"][0]["text"]
                # Robust JSON cleaning (remove markdown etc)
                json_match = re.search(r"(\[.*\])", content, re.DOTALL)
                if json_match:
                    content = json_match.group(1)
                
                gemini_breaker.record_success()
                return json.loads(content)
        except Exception as exc:
            logger.warning(f"AI Metadata Suggestion failed on {model}: {exc}")
            continue
            
    gemini_breaker.record_failure()
    return []


async def suggest_token_description(name: str, symbol: str, theme: str = "") -> str:
    """Generate a professional description with tiered fallback resilience."""
    if not gemini_breaker.is_available():
         return ""
         
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key: return ""

    prompt = f"""
    Write a short, viral-ready, and professional meme token description for {symbol} ({name}).
    Context: {theme}
    Tone: Degen-friendly, high-conviction, Base network 'moon mission' vibe.
    Constraint: Plain text, 150-250 characters, NO hashtags.
    """
    
    for model in ["gemini-1.5-flash-latest", "gemini-1.5-flash-8b"]:
        try:
            await gemini_limiter.wait()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 429: continue
                resp.raise_for_status()
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                gemini_breaker.record_success()
                return text.replace("```", "").strip(' "')
        except Exception:
            continue
            
    gemini_breaker.record_failure()
    return f"🚀 {name} (${symbol}) - A community-driven token launching on the Base network. Clank and Claw verified. Join the movement!"
