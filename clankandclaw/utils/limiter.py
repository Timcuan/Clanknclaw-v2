import asyncio
import time


class AsyncRateLimiter:
    """Token-bucket rate limiter for asynchronous tasks."""

    def __init__(self, requests_per_minute: float):
        self.requests_per_minute = requests_per_minute
        self.interval = 60.0 / requests_per_minute if requests_per_minute > 0 else 0
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """Wait until enough time has passed to perform a call."""
        if self.requests_per_minute <= 0:
            return

        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self.interval:
                await asyncio.sleep(self.interval - elapsed)
            self._last_call = time.monotonic()


# Global Singleton for Gemini API
gemini_limiter = AsyncRateLimiter(requests_per_minute=10.0)
