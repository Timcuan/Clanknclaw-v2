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
GeminiProfile: TypeAlias = str


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

    def reset(self):
        with self._lock:
            self.failures = 0
            self.last_failure_time = 0.0

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
_gemini_client: httpx.AsyncClient | None = None
_gemini_client_lock = asyncio.Lock()


async def _get_gemini_client() -> httpx.AsyncClient:
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    async with _gemini_client_lock:
        if _gemini_client is None:
            _gemini_client = httpx.AsyncClient(timeout=30.0)
    return _gemini_client


async def _reset_gemini_client_for_tests() -> None:
    global _gemini_client
    async with _gemini_client_lock:
        if _gemini_client is not None:
            await _gemini_client.aclose()
            _gemini_client = None


def _build_model_tiers(profile: GeminiProfile = "lite") -> list[str]:
    configured_model = (os.getenv("GEMINI_MODEL") or "").strip()
    configured_lite = (os.getenv("GEMINI_MODEL_LITE") or "").strip()
    configured_flash = (os.getenv("GEMINI_MODEL_FLASH") or "").strip()

    if profile == "flash":
        preferred = [
            configured_model,
            configured_flash,
            "models/gemini-2.5-flash",
            "models/gemini-flash-latest",
            "models/gemini-2.5-flash-lite",
        ]
    else:
        preferred = [
            configured_model,
            configured_lite,
            "models/gemini-2.5-flash-lite",
            "models/gemini-flash-lite-latest",
            "models/gemini-2.5-flash",
            "models/gemini-flash-latest",
        ]

    tiers: list[str] = []
    for model_path in preferred:
        if not model_path:
            continue
        if model_path not in tiers:
            tiers.append(model_path)
    return tiers


async def _call_gemini_api(
    prompt: str,
    json_mode: bool = True,
    *,
    profile: GeminiProfile = "lite",
) -> str:
    """Unified, resilient, and multi-tier Gemini calling engine."""
    if not gemini_breaker.is_available():
         return ""
         
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return ""

    # Tiered execution strategy with resilient fallback when a model is retired.
    tiers = _build_model_tiers(profile=profile)
    client = await _get_gemini_client()
    for model_path in tiers:
        try:
            await gemini_limiter.wait()
            url = f"https://generativelanguage.googleapis.com/v1beta/{model_path}:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "response_mime_type": "application/json" if json_mode else "text/plain"
                }
            }

            resp = await client.post(url, json=payload)

            if resp.status_code == 429: # Rate Limit
                 logger.warning(f"Gemini {model_path} Rate Limited (429). Trying next tier...")
                 continue
            if resp.status_code == 403: # API Key Restricted
                 logger.error(f"Gemini {model_path} FORBIDDEN (403). CHECK API KEY.")
                 gemini_breaker.record_failure()
                 return ""
            if resp.status_code == 503: # Overloaded
                 logger.warning(f"Gemini {model_path} Overloaded (503). Trying next tier...")
                 continue
            if resp.status_code == 400: # Bad Request
                 logger.error(f"Gemini {model_path} 400 BAD REQUEST: {resp.text}")
                 continue
            if resp.status_code == 404: # Model not found / retired
                 logger.warning(f"Gemini {model_path} NOT FOUND (404). Trying next tier...")
                 continue

            resp.raise_for_status()
            data = resp.json()
            content = data["candidates"][0]["content"]["parts"][0]["text"]
            gemini_breaker.record_success()
            return content

        except Exception as exc:
            logger.warning(f"Gemini call to {model_path} failed: {exc}")
            continue

    gemini_breaker.record_failure()
    return ""


async def extract_token_identity_with_llm(text: str) -> TokenIdentity:
    """Tiered extraction: Gemini 3.x -> Heuristic fallback."""
    prompt = f"Extract 'name' and 'symbol' (ticker) from this text. Return JSON only: {text}"
    content = await _call_gemini_api(prompt, profile="lite")
    
    if not content:
        return await _extract_heuristic(text)
        
    try:
        # Robust JSON cleaning
        json_match = re.search(r"({.*})", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        obj = json.loads(content)
        return _clean_best_effort(obj.get("name", "Unknown"))[:50], _clean_best_effort(obj.get("symbol", "TKN"))[:10]
    except Exception:
        return await _extract_heuristic(text)


async def enrich_signal_with_llm(text: str) -> dict[str, Any]:
    """Unified call for maximum efficiency."""
    prompt = f"""
    Analyze this social media post and return a JSON object with the following fields:
    - symbol: The official token symbol/ticker (null if not found).
    - is_genuine_launch: Boolean. Is the user actually trying to deploy or requesting a launch?
    - bullish_score: Integer (1-100). Score > 80 if it has 'Alpha' potential (minimalist tickers, community intent).
    - ai_rationale: One-sentence degen explanation of why this is a moon-mission or a skip.
    - suggested_description: A viral, degen-friendly 150-200 char description.

    Text: {text}
    """
    content = await _call_gemini_api(prompt, profile="lite")
    if not content: return {}
    
    try:
        json_match = re.search(r"({.*})", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        return json.loads(content)
    except Exception:
        return {}


async def validate_gecko_candidate_with_llm(
    token_name: str,
    token_symbol: str,
    volume_m5: float,
    liquidity: float,
    age_minutes: float,
    scan_mode: str,
) -> dict[str, Any]:
    """
    LLM quality gate for Gecko auto-deploy candidates.
    
    Validates name/symbol sanity, detects scam/offensive patterns,
    and generates a deploy-ready description.
    
    Returns dict with keys:
      - safe: bool (True = proceed, False = flag for review)
      - risk: None | "scam" | "offensive" | "generic" | "suspicious"
      - description: str (150-char deploy description)
    
    Always returns a safe default on timeout/error (non-blocking).
    """
    _DEFAULT = {"safe": True, "risk": None, "description": ""}

    if not gemini_breaker.is_available():
        return _DEFAULT

    mode_label = "early new launch" if scan_mode == "new_pools" else "trending pool"
    liq_str = f"${liquidity:,.0f}"
    vol_str = f"${volume_m5:,.0f}"
    age_str = f"{age_minutes:.0f}m" if age_minutes < 999 else "unknown"

    prompt = f"""You are a crypto token quality analyst for the Base network.

Token detected: {token_name} (${token_symbol})
Context: {mode_label} on Base DEX. Age: {age_str}. Vol 5m: {vol_str}. Liquidity: {liq_str}.

Tasks:
1. Is the name "{token_name}" legitimate? (not offensive, scam-like, or meaningless)
2. Is "${token_symbol}" valid? (2-10 uppercase chars, not an obvious copy or scam ticker)
3. Write a concise 120-150 char description for this token launch on Base.
4. Any red flags? Examples: known rug patterns, offensive content, obvious copy of major token.

Return ONLY valid JSON:
{{"safe": true|false, "risk": null|"scam"|"offensive"|"suspicious"|"generic", "description": "..."}}

Be permissive: only set safe=false for clear red flags. Meme names are fine."""

    try:
        content = await asyncio.wait_for(
            _call_gemini_api(prompt, json_mode=True, profile="lite"),
            timeout=5.0,
        )
        if not content:
            return _DEFAULT
        json_match = re.search(r"(\{.*\})", content, re.DOTALL)
        if not json_match:
            return _DEFAULT
        result = json.loads(json_match.group(1))
        return {
            "safe": bool(result.get("safe", True)),
            "risk": result.get("risk") or None,
            "description": str(result.get("description") or "")[:200],
        }
    except (asyncio.TimeoutError, Exception) as exc:
        logger.debug("validate_gecko_candidate_with_llm skipped: %s", exc)
        return _DEFAULT


async def _extract_heuristic(text: str) -> TokenIdentity:
    """Robust Heuristic Fallback Engine (Non-LLM)."""
    words = text.split()
    if not words:
        return "Unknown", "TKN"
        
    tickers = [w.strip("$!?,.") for w in words if w.isupper() and 3 <= len(w.strip("$!?,.")) <= 8]
    symbol = tickers[-1] if tickers else "TKN"
    
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
        except BaseException as exc:
            error["value"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()

    if "value" in error:
        raise error["value"]
    return result["value"]


async def suggest_token_metadata(theme: str) -> list[dict[str, str]]:
    """Suggest creative and themed name/ticker pairs."""
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
    content = await _call_gemini_api(prompt, profile="flash")
    if not content: return []
    
    try:
        json_match = re.search(r"(\[.*\])", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        return json.loads(content)
    except Exception:
        return []


async def suggest_token_description(name: str, symbol: str, theme: str = "") -> str:
    """Generate a professional description."""
    prompt = f"""
    Write a short, viral-ready, and degen-friendly meme token description for {symbol} ({name}).
    Context: {theme}
    Tone: Banter-heavy, high-conviction, Base network 'moon mission' vibe (WAGMI, LFG).
    Constraint: Plain text, 150-250 characters, NO hashtags.
    """
    content = await _call_gemini_api(prompt, profile="flash")
    if not content:
        return f"🚀 {name} (${symbol}) - A community-driven token launching on the Base network. Clank and Claw verified. Join the movement!"
    
    return content.replace("```", "").strip(' "')
