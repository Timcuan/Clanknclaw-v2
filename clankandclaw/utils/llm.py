import asyncio
import inspect
import threading
from collections.abc import Awaitable, Callable
from typing import TypeAlias


TokenIdentity: TypeAlias = tuple[str, str]
TokenIdentityFallback: TypeAlias = Callable[[str], TokenIdentity | Awaitable[TokenIdentity]]


async def extract_token_identity_with_llm(text: str) -> TokenIdentity:
    raise NotImplementedError("LLM fallback is not implemented in the MVP seed")


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
