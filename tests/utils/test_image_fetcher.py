import io
import socket
import ssl

import pytest

from clankandclaw.utils.image_fetcher import MAX_IMAGE_BYTES, fetch_image_bytes


def _getaddrinfo_stub(hostname_to_ip: dict[str, str]):
    def _stub(host: str, port: int, *args, **kwargs):
        resolved_ip = hostname_to_ip.get(host, "93.184.216.34")
        if ":" in resolved_ip:
            return [
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (resolved_ip, port, 0, 0),
                )
            ]

        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (resolved_ip, port),
            )
        ]

    return _stub


def _build_http_response(
    *,
    status_code: int = 200,
    reason: str = "OK",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> bytes:
    response_headers = dict(headers or {})
    response_headers.setdefault("Content-Length", str(len(body)))

    header_block = "".join(f"{name}: {value}\r\n" for name, value in response_headers.items())
    status_line = f"HTTP/1.1 {status_code} {reason}\r\n"
    return (status_line + header_block + "\r\n").encode("ascii") + body


def _build_chunked_http_response(
    chunks: list[bytes],
    *,
    status_code: int = 200,
    reason: str = "OK",
    headers: dict[str, str] | None = None,
) -> bytes:
    response_headers = dict(headers or {})
    response_headers["Transfer-Encoding"] = "chunked"

    header_block = "".join(f"{name}: {value}\r\n" for name, value in response_headers.items())
    status_line = f"HTTP/1.1 {status_code} {reason}\r\n"

    body = b"".join(
        f"{len(chunk):X}\r\n".encode("ascii") + chunk + b"\r\n" for chunk in chunks
    ) + b"0\r\n\r\n"

    return (status_line + header_block + "\r\n").encode("ascii") + body


class _FakeSocket:
    def __init__(self, response_bytes: bytes):
        self._response_bytes = response_bytes
        self.sent_data = bytearray()
        self.closed = False

    def sendall(self, data: bytes) -> None:
        self.sent_data.extend(data)

    def makefile(self, mode: str, buffering: int | None = None):
        assert mode == "rb"
        return io.BytesIO(self._response_bytes)

    def close(self) -> None:
        self.closed = True


class _SocketFactory:
    def __init__(self, responses_by_target: dict[tuple[str, int], list[bytes]]):
        self._responses_by_target = {
            target: list(response_bytes)
            for target, response_bytes in responses_by_target.items()
        }
        self.calls: list[tuple[str, int]] = []
        self.sockets: list[_FakeSocket] = []

    def create_connection(self, address: tuple[str, int], timeout: float | None = None, source_address=None):
        del timeout
        del source_address
        self.calls.append(address)

        response_queue = self._responses_by_target.get(address)
        if not response_queue:
            raise AssertionError(f"unexpected connection target: {address}")

        sock = _FakeSocket(response_queue.pop(0))
        self.sockets.append(sock)
        return sock


class _SSLContextStub:
    def __init__(self):
        self.server_hostnames: list[str | None] = []

    def wrap_socket(self, sock: _FakeSocket, *, server_hostname: str | None = None):
        self.server_hostnames.append(server_hostname)
        return sock


@pytest.fixture(autouse=True)
def _stub_safe_dns_resolution(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo_stub({}))


@pytest.mark.asyncio
async def test_fetch_image_bytes_returns_response_content_using_validated_ip(
    monkeypatch: pytest.MonkeyPatch,
):
    socket_factory = _SocketFactory(
        {
            ("93.184.216.34", 443): [
                _build_http_response(
                    headers={"Content-Type": "image/png"},
                    body=b"image-bytes",
                )
            ]
        }
    )
    ssl_context = _SSLContextStub()
    monkeypatch.setattr(socket, "create_connection", socket_factory.create_connection)
    monkeypatch.setattr(ssl, "create_default_context", lambda: ssl_context)

    result = await fetch_image_bytes("https://example.com/image.png")

    assert result == b"image-bytes"
    assert socket_factory.calls == [("93.184.216.34", 443)]
    assert ssl_context.server_hostnames == ["example.com"]
    assert b"Host: example.com\r\n" in bytes(socket_factory.sockets[0].sent_data)
    assert b"GET /image.png HTTP/1.1\r\n" in bytes(socket_factory.sockets[0].sent_data)


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_unsafe_url():
    with pytest.raises(ValueError, match="unsafe image URL"):
        await fetch_image_bytes("http://127.0.0.1/image.png")


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_hostname_resolving_to_unsafe_address(
    monkeypatch: pytest.MonkeyPatch,
):
    socket_factory = _SocketFactory({})
    monkeypatch.setattr(socket, "create_connection", socket_factory.create_connection)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _getaddrinfo_stub({"internal.example": "10.0.0.5"}),
    )

    with pytest.raises(ValueError, match="unsafe image URL"):
        await fetch_image_bytes("https://internal.example/image.png")

    assert socket_factory.calls == []


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_non_image_content_type(
    monkeypatch: pytest.MonkeyPatch,
):
    socket_factory = _SocketFactory(
        {
            ("93.184.216.34", 443): [
                _build_http_response(
                    headers={"Content-Type": "text/html"},
                    body=b"not-an-image",
                )
            ]
        }
    )
    ssl_context = _SSLContextStub()
    monkeypatch.setattr(socket, "create_connection", socket_factory.create_connection)
    monkeypatch.setattr(ssl, "create_default_context", lambda: ssl_context)

    with pytest.raises(ValueError, match="image content type"):
        await fetch_image_bytes("https://example.com/not-image")


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_oversized_payload(
    monkeypatch: pytest.MonkeyPatch,
):
    socket_factory = _SocketFactory(
        {
            ("93.184.216.34", 443): [
                _build_http_response(
                    headers={
                        "Content-Type": "image/png",
                        "Content-Length": str(MAX_IMAGE_BYTES + 1),
                    },
                    body=b"x",
                )
            ]
        }
    )
    ssl_context = _SSLContextStub()
    monkeypatch.setattr(socket, "create_connection", socket_factory.create_connection)
    monkeypatch.setattr(ssl, "create_default_context", lambda: ssl_context)

    with pytest.raises(ValueError, match="too large"):
        await fetch_image_bytes("https://example.com/huge-image.png")


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_redirect_chain_to_unsafe_host(
    monkeypatch: pytest.MonkeyPatch,
):
    socket_factory = _SocketFactory(
        {
            ("93.184.216.34", 443): [
                _build_http_response(
                    status_code=302,
                    reason="Found",
                    headers={"Location": "http://127.0.0.1/private.png"},
                )
            ]
        }
    )
    ssl_context = _SSLContextStub()
    monkeypatch.setattr(socket, "create_connection", socket_factory.create_connection)
    monkeypatch.setattr(ssl, "create_default_context", lambda: ssl_context)

    with pytest.raises(ValueError, match="unsafe image URL"):
        await fetch_image_bytes("https://example.com/start.png")

    assert socket_factory.calls == [("93.184.216.34", 443)]


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_redirect_hostname_resolving_to_unsafe_address(
    monkeypatch: pytest.MonkeyPatch,
):
    socket_factory = _SocketFactory(
        {
            ("93.184.216.34", 443): [
                _build_http_response(
                    status_code=302,
                    reason="Found",
                    headers={"Location": "https://redirect.internal.example/private.png"},
                )
            ]
        }
    )
    ssl_context = _SSLContextStub()
    monkeypatch.setattr(socket, "create_connection", socket_factory.create_connection)
    monkeypatch.setattr(ssl, "create_default_context", lambda: ssl_context)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _getaddrinfo_stub(
            {
                "example.com": "93.184.216.34",
                "redirect.internal.example": "169.254.169.254",
            }
        ),
    )

    with pytest.raises(ValueError, match="unsafe image URL"):
        await fetch_image_bytes("https://example.com/start.png")

    assert socket_factory.calls == [("93.184.216.34", 443)]


@pytest.mark.asyncio
async def test_fetch_image_bytes_revalidates_redirect_and_connects_to_redirect_ip(
    monkeypatch: pytest.MonkeyPatch,
):
    socket_factory = _SocketFactory(
        {
            ("93.184.216.34", 443): [
                _build_http_response(
                    status_code=302,
                    reason="Found",
                    headers={"Location": "https://cdn.example.net/assets/final.png"},
                )
            ],
            ("151.101.1.69", 443): [
                _build_http_response(
                    headers={"Content-Type": "image/png"},
                    body=b"redirected-image",
                )
            ],
        }
    )
    ssl_context = _SSLContextStub()
    monkeypatch.setattr(socket, "create_connection", socket_factory.create_connection)
    monkeypatch.setattr(ssl, "create_default_context", lambda: ssl_context)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _getaddrinfo_stub(
            {
                "example.com": "93.184.216.34",
                "cdn.example.net": "151.101.1.69",
            }
        ),
    )

    result = await fetch_image_bytes("https://example.com/start.png")

    assert result == b"redirected-image"
    assert socket_factory.calls == [
        ("93.184.216.34", 443),
        ("151.101.1.69", 443),
    ]
    assert ssl_context.server_hostnames == ["example.com", "cdn.example.net"]
    assert b"Host: example.com\r\n" in bytes(socket_factory.sockets[0].sent_data)
    assert b"Host: cdn.example.net\r\n" in bytes(socket_factory.sockets[1].sent_data)


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_oversized_streaming_payload_without_trustworthy_length(
    monkeypatch: pytest.MonkeyPatch,
):
    socket_factory = _SocketFactory(
        {
            ("93.184.216.34", 443): [
                _build_chunked_http_response(
                    [b"x" * MAX_IMAGE_BYTES, b"y", b"z"],
                    headers={"Content-Type": "image/png", "Content-Length": "invalid"},
                )
            ]
        }
    )
    ssl_context = _SSLContextStub()
    monkeypatch.setattr(socket, "create_connection", socket_factory.create_connection)
    monkeypatch.setattr(ssl, "create_default_context", lambda: ssl_context)

    with pytest.raises(ValueError, match="too large"):
        await fetch_image_bytes("https://example.com/streamed-image.png")
