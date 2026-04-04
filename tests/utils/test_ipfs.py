import pytest

from clankandclaw.utils.ipfs import PinataClient


def test_pinata_client_reads_jwt_from_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PINATA_JWT", "token-from-env")

    client = PinataClient()

    assert client.jwt == "token-from-env"


def test_pinata_client_requires_jwt(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PINATA_JWT", raising=False)

    with pytest.raises(ValueError, match="PINATA_JWT is required"):
        PinataClient()
