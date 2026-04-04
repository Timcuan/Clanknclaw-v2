import os

import httpx


class PinataClient:
    def __init__(self, jwt: str | None = None):
        self.jwt = jwt or os.getenv("PINATA_JWT")
        if not self.jwt:
            raise ValueError("PINATA_JWT is required")
