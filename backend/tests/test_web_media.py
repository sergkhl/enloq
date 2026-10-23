"""Authored wire peers exercise the real HTTP parser without any live network."""

import asyncio
import logging
import socket
import ssl
from collections import deque
from pathlib import Path

import httpcore
import pytest
from enloq.domain.models import MediaTooLarge, Refused, Unavailable
from enloq.transport import web_media

PUBLIC = "93.184.216.34"
OTHER_PUBLIC = "8.8.8.8"
SELECTED = "https://gif.example/exact.gif?private-query=authored-only"
BYTES = b"GIF89a\x00\x01authored original bytes\xff\x00"


def wire(body=BYTES, *, status=200, headers=(), length=True):
    fields = list(headers)
    if length:
        fields.append((b"Content-Length", str(len(body)).encode()))
    return b"\r\n".join(
        [f"HTTP/1.1 {status} Test".encode()]
        + [key + b": " + value for key, value in fields]
        + [b"", body]
    )


class Peer(httpcore.AsyncNetworkStream):
    def __init__(
        self, response, *, peer=PUBLIC, port=443, block=False, error=None, chunk=65536
    ):
        self.response = response
        self.peer = peer
        self.port = port
        self.block = block
        self.error = error
        self.chunk = chunk
        self.writes = []
        self.tls = []
        self.closed = False
        self.read_count = 0
        self.started = asyncio.Event()

    async def read(self, max_bytes, timeout=None):
        self.read_count += 1
        self.started.set()
        if self.block:
            await asyncio.Future()
        if self.error:
            raise self.error
        data = self.response[: min(max_bytes, self.chunk)]
        self.response = self.response[len(data) :]
        return data

    async def write(self, buffer, timeout=None):
        self.writes.append(buffer)

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.tls.append((server_hostname, ssl_context, timeout))
        return self

    async def aclose(self):
        self.closed = True

    def get_extra_info(self, name):
        if name == "server_addr":
            return self.peer, self.port


class Network(httpcore.AsyncNetworkBackend):
    def __init__(self, peers):
        self.peers = deque(peers)
        self.calls = []

    async def connect_tcp(self, host, port, **options):
        self.calls.append((host, port, options))
        return self.peers.popleft()


@pytest.fixture
def fake_network(monkeypatch):
    def install(*peers, dns=None):
        network = Network(peers)
        resolutions = []

        async def resolve(host, port):
            resolutions.append((host, port))
            return dns.get(host, [PUBLIC]) if dns is not None else [PUBLIC]

        monkeypatch.setattr(web_media, "_network_backend", lambda: network)
        monkeypatch.setattr(web_media, "_resolve", resolve)
        return network, resolutions

    return install


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://gif.example/a",
        "data:image/gif;base64,AA==",
        "javascript:alert(1)",
        "//gif.example/a",
        "https:///a",
        "https://user:password@gif.example/a",
        "https://@gif.example/a",
        "https://gif.example:8080/a",
        "https://gif.example:0/a",
        "https://gif.example:65536/a",
        "https://gif.example:wrong/a",
        "https://localhost/a",
        "https://host.local/a",
        "https://host.internal/a",
        "https://host.localhost/a",
        "https://host.onion/a",
        "https://127.0.0.1/a",
        "https://127.1/a",
        "https://2130706433/a",
        "https://10.0.0.1/a",
        "http://169.254.169.254/latest/meta-data",
        "http://100.100.100.200/a",
        "https://0.0.0.0/a",
        "https://224.0.0.1/a",
        "https://[::1]/a",
        "https://[::ffff:8.8.8.8]/a",
        "https://[fe80::1%25en0]/a",
        "https://[64:ff9b::a00:1]/a",
        "https://gif.example/a#fragment",
        "https://gif.example/a\r\nInjected:yes",
        "https://gif.example\\@127.0.0.1/a",
        "https://gif.example/a%ZZ",
        "https://%67if.example/a",
        "https://bad_name.example/a",
        "https://gif.example/" + "x" * 8192,
        None,
        42,
        b"https://gif.example/a",
    ],
)
def test_obviously_unsafe_urls_refused_without_network(url):
    with pytest.raises(Refused, match="cannot be fetched safely") as refused:
        web_media.validate_web_url(url)
    assert str(refused.value) == "Selected web GIF cannot be fetched safely"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("HTTPS://GIF.Example:443/a%2Fb?x=1&x=2", "https://gif.example/a%2Fb?x=1&x=2"),
        ("http://gif.example:80", "http://gif.example/"),
        ("https://gif.example/chosen?", "https://gif.example/chosen?"),
        (
            "https://gif.example/привет?x=é",
            "https://gif.example/%D0%BF%D1%80%D0%B8%D0%B2%D0%B5%D1%82?x=%C3%A9",
        ),
        ("https://8.8.8.8/a", "https://8.8.8.8/a"),
        ("https://[2606:4700:4700::1111]/a", "https://[2606:4700:4700::1111]/a"),
    ],
)
def test_url_normalization_preserves_selected_resource(url, expected):
    assert web_media.validate_web_url(url) == expected


async def test_exact_identity_bytes_private_file_original_host_tls_and_no_credentials(
    tmp_path, fake_network, monkeypatch
):
    # Environment proxies must never change this direct pinned connection.
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    peer = Peer(wire(headers=[(b"Content-Type", b"image/gif; authored=metadata")]))
    network, resolutions = fake_network(peer)
    path = tmp_path / "chosen"
    result = await web_media.fetch_web_media(SELECTED, path)
    assert result == web_media.WebMediaFile(len(BYTES), "image/gif")
    assert path.read_bytes() == BYTES
    assert path.stat().st_mode & 0o777 == 0o600
    assert resolutions == [("gif.example", 443)]
    assert [(host, port) for host, port, _ in network.calls] == [(PUBLIC, 443)]
    assert network.calls[0][2]["timeout"] == 10
    hostname, context, timeout = peer.tls[0]
    assert hostname == "gif.example"
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert timeout == 10
    request = b"".join(peer.writes)
    assert b"GET /exact.gif?private-query=authored-only HTTP/1.1\r\n" in request
    assert b"Host: gif.example\r\n" in request
    assert b"Accept-Encoding: identity\r\n" in request
    for header in (b"authorization:", b"cookie:", b"referer:", b"range:"):
        assert header not in request.lower()
    assert peer.closed


async def test_rebinding_cannot_replace_the_validated_tcp_destination(
    tmp_path, fake_network, monkeypatch
):
    peer = Peer(wire())
    network, _ = fake_network(peer)
    calls = []

    async def rebind(host, port):
        calls.append(host)
        return [PUBLIC] if len(calls) == 1 else ["127.0.0.1"]

    monkeypatch.setattr(web_media, "_resolve", rebind)
    await web_media.fetch_web_media(SELECTED, tmp_path / "chosen")
    assert calls == ["gif.example"]
    assert network.calls[0][0] == PUBLIC


@pytest.mark.parametrize(
    "addresses",
    [
        [],
        ["127.0.0.1"],
        ["10.0.0.1"],
        ["169.254.169.254"],
        ["100.100.100.200"],
        ["100.64.0.1"],
        ["224.0.0.1"],
        ["0.0.0.0"],
        ["192.0.2.1"],
        ["240.0.0.1"],
        ["::1"],
        ["fe80::1"],
        ["fc00::1"],
        ["fec0::1"],
        ["ff02::1"],
        ["::ffff:8.8.8.8"],
        ["2002:0808:0808::1"],
        ["64:ff9b::808:808"],
        ["64:ff9b:1::1"],
        [PUBLIC, "127.0.0.1"],
        [PUBLIC] * 33,
    ],
)
async def test_every_dns_answer_must_be_public_before_any_connection(
    addresses, tmp_path, fake_network
):
    network, _ = fake_network(dns={"gif.example": addresses})
    path = tmp_path / "chosen"
    with pytest.raises(Refused):
        await web_media.fetch_web_media(SELECTED, path)
    assert network.calls == []
    assert not path.exists()


@pytest.mark.parametrize(
    "wrong_peer", ["127.0.0.1", OTHER_PUBLIC, "::ffff:93.184.216.34"]
)
async def test_actual_peer_must_match_pinned_public_destination(
    wrong_peer, tmp_path, fake_network
):
    peer = Peer(wire(), peer=wrong_peer)
    fake_network(peer)
    path = tmp_path / "chosen"
    with pytest.raises(Refused):
        await web_media.fetch_web_media(SELECTED, path)
    assert not path.exists()
    assert peer.closed
    assert peer.writes == []
    assert peer.tls == []


async def test_each_redirect_is_revalidated_and_new_destination_is_pinned(
    tmp_path, fake_network
):
    first = Peer(
        wire(
            status=302,
            headers=[(b"Location", b"https://cdn.example/selected.mp4?exact=2")],
        )
    )
    final = Peer(wire(headers=[(b"Content-Type", b"video/mp4")]), peer=OTHER_PUBLIC)
    network, resolutions = fake_network(
        first, final, dns={"gif.example": [PUBLIC], "cdn.example": [OTHER_PUBLIC]}
    )
    path = tmp_path / "chosen"
    assert await web_media.fetch_web_media(SELECTED, path) == web_media.WebMediaFile(
        len(BYTES), "video/mp4"
    )
    assert path.read_bytes() == BYTES
    assert resolutions == [("gif.example", 443), ("cdn.example", 443)]
    assert [call[0] for call in network.calls] == [PUBLIC, OTHER_PUBLIC]
    assert final.tls[0][0] == "cdn.example"
    assert b"GET /selected.mp4?exact=2 HTTP/1.1" in b"".join(final.writes)
    assert b"Host: cdn.example" in b"".join(final.writes)


@pytest.mark.parametrize(
    "location",
    [
        b"http://gif.example/a",
        b"file:///etc/passwd",
        b"ftp://cdn.example/a",
        b"https://127.0.0.1/a",
        b"//169.254.169.254/a",
        b"https://user:secret@cdn.example/a",
        b"https://host.local/a",
        b"https://cdn.example:8080/a",
        b"https://cdn.example/a%ZZ",
        b"https://gif.example/exact.gif?private-query=authored-only",
        b"",
        b"/" + b"x" * 8192,
    ],
)
async def test_unsafe_redirect_does_not_connect_to_its_target(
    location, tmp_path, fake_network
):
    peer = Peer(wire(status=302, headers=[(b"Location", location)]))
    network, _ = fake_network(peer)
    path = tmp_path / "chosen"
    with pytest.raises(Refused):
        await web_media.fetch_web_media(SELECTED, path)
    assert len(network.calls) == 1
    assert not path.exists()
    assert peer.closed


async def test_redirect_dns_with_private_answer_is_rejected(tmp_path, fake_network):
    peer = Peer(wire(status=307, headers=[(b"Location", b"https://cdn.example/a")]))
    network, resolutions = fake_network(
        peer, dns={"gif.example": [PUBLIC], "cdn.example": [PUBLIC, "::1"]}
    )
    with pytest.raises(Refused):
        await web_media.fetch_web_media(SELECTED, tmp_path / "chosen")
    assert resolutions == [("gif.example", 443), ("cdn.example", 443)]
    assert len(network.calls) == 1


async def test_redirect_count_bounded_and_bodies_not_consumed(tmp_path, fake_network):
    peers = [
        Peer(
            wire(
                body=b"z" * 200000,
                status=302,
                headers=[(b"Location", f"/next-{index}".encode())],
            )
        )
        for index in range(4)
    ]
    network, _ = fake_network(*peers)
    with pytest.raises(Refused):
        await web_media.fetch_web_media(SELECTED, tmp_path / "chosen")
    assert len(network.calls) == 4
    assert all(peer.read_count == 1 and peer.response and peer.closed for peer in peers)


async def test_relative_redirect_preserves_exact_target_without_reusing_dns(
    tmp_path, fake_network
):
    first = Peer(wire(status=303, headers=[(b"Location", b"/chosen?result=exact")]))
    final = Peer(wire())
    _, resolutions = fake_network(first, final)
    await web_media.fetch_web_media(SELECTED, tmp_path / "chosen")
    assert resolutions == [("gif.example", 443), ("gif.example", 443)]
    assert b"GET /chosen?result=exact HTTP/1.1" in b"".join(final.writes)


async def test_http_to_https_redirect_is_supported_without_auth_or_cookies(
    tmp_path, fake_network
):
    first = Peer(
        wire(
            status=308,
            headers=[
                (b"Location", b"https://cdn.example/chosen?"),
                (b"Set-Cookie", b"private=do-not-forward"),
            ],
        ),
        port=80,
    )
    final = Peer(wire())
    network, resolutions = fake_network(first, final)
    await web_media.fetch_web_media("http://gif.example/selected", tmp_path / "chosen")
    assert resolutions == [("gif.example", 80), ("cdn.example", 443)]
    assert [call[1] for call in network.calls] == [80, 443]
    assert first.tls == []
    assert final.tls[0][0] == "cdn.example"
    request = b"".join(final.writes)
    assert b"GET /chosen? HTTP/1.1" in request
    assert b"cookie" not in request.lower()


async def test_public_ipv6_literal_pins_socket_and_has_bracketed_host(
    tmp_path, fake_network
):
    address = "2606:4700:4700::1111"
    peer = Peer(wire(), peer=address)
    network, resolutions = fake_network(peer)
    await web_media.fetch_web_media(f"https://[{address}]/chosen", tmp_path / "chosen")
    assert resolutions == []
    assert network.calls[0][0] == address
    assert peer.tls[0][0] == address
    assert f"Host: [{address}]\r\n".encode() in b"".join(peer.writes)


@pytest.mark.parametrize("status", [204, 206, 304, 400, 403, 404, 500])
async def test_only_full_successful_entity_is_retained(status, tmp_path, fake_network):
    fake_network(Peer(wire(status=status)))
    path = tmp_path / "chosen"
    with pytest.raises(Unavailable, match="Selected web GIF is unavailable"):
        await web_media.fetch_web_media(SELECTED, path)
    assert not path.exists()


@pytest.mark.parametrize(
    "headers",
    [
        [(b"Content-Type", b"text/html")],
        [(b"Content-Type", b"application/json")],
        [(b"Content-Type", b"image/svg+xml")],
        [(b"Content-Type", b"image/jpeg")],
        [(b"Content-Encoding", b"gzip")],
        [(b"Content-Encoding", b"br")],
        [(b"Content-Encoding", b"identity, gzip")],
        [(b"Content-Type", b"image/gif"), (b"Content-Type", b"video/mp4")],
        [(b"Content-Encoding", b"identity"), (b"Content-Encoding", b"identity")],
    ],
)
async def test_content_admission_rejects_pages_duplicates_and_encoding(
    headers, tmp_path, fake_network
):
    fake_network(Peer(wire(headers=headers)))
    path = tmp_path / "chosen"
    with pytest.raises(Refused):
        await web_media.fetch_web_media(SELECTED, path)
    assert not path.exists()


@pytest.mark.parametrize(
    "mime", [None, b"application/octet-stream", b"binary/octet-stream", b"video/mp4"]
)
async def test_mime_is_only_metadata_and_never_substitutes_or_transforms_bytes(
    mime, tmp_path, fake_network
):
    # This is deliberately not a real GIF. Byte-format proof belongs to the
    # selected-media verifier, not URL/filename/Content-Type labels.
    original = b"authored bytes needing independent format proof"
    headers = [(b"Content-Type", mime)] if mime else []
    fake_network(Peer(wire(original, headers=headers)))
    path = tmp_path / "chosen"
    result = await web_media.fetch_web_media(SELECTED, path)
    assert result.mime == (mime.decode() if mime else None)
    assert path.read_bytes() == original


@pytest.mark.parametrize("declared", [b"51", str(50 * 1024**2 + 1).encode(), b"9" * 11])
async def test_declared_size_refused_before_consuming_body(
    declared, tmp_path, fake_network
):
    peer = Peer(wire(headers=[(b"Content-Length", declared)], length=False))
    fake_network(peer)
    path = tmp_path / "chosen"
    with pytest.raises(MediaTooLarge):
        await web_media.fetch_web_media(SELECTED, path, limit=50)
    assert not path.exists()
    assert peer.read_count == 1


@pytest.mark.parametrize("declared", [b"9" * 100, b"-1", b"+5", b"not-a-size"])
async def test_malformed_length_is_private_refusal_and_leaves_no_file(
    declared, tmp_path, fake_network
):
    fake_network(Peer(wire(headers=[(b"Content-Length", declared)], length=False)))
    path = tmp_path / "chosen"
    with pytest.raises(Unavailable) as refused:
        await web_media.fetch_web_media(SELECTED, path)
    assert str(refused.value) == "Selected web GIF is unavailable"
    assert not path.exists()


async def test_default_limit_is_fifty_mib_and_cannot_be_raised(tmp_path, fake_network):
    peer = Peer(
        wire(
            headers=[(b"Content-Length", str(50 * 1024**2 + 1).encode())], length=False
        )
    )
    network, _ = fake_network(peer)
    with pytest.raises(MediaTooLarge):
        await web_media.fetch_web_media(SELECTED, tmp_path / "chosen")
    assert web_media.GIF_BYTES == 50 * 1024**2
    calls = len(network.calls)
    with pytest.raises(Refused):
        await web_media.fetch_web_media(
            SELECTED, tmp_path / "chosen", limit=web_media.GIF_BYTES + 1
        )
    assert len(network.calls) == calls


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
async def test_invalid_caller_limit_does_no_network_or_file_work(
    limit, tmp_path, fake_network
):
    network, _ = fake_network()
    path = tmp_path / "chosen"
    with pytest.raises(Refused):
        await web_media.fetch_web_media(SELECTED, path, limit=limit)
    assert not path.exists()
    assert network.calls == []


@pytest.mark.parametrize("chunked", [False, True])
async def test_actual_size_bound_applies_without_content_length(
    chunked, tmp_path, fake_network
):
    body = b"12345" + b"67890"
    if chunked:
        body = b"5\r\n12345\r\n5\r\n67890\r\n0\r\n\r\n"
    headers = [(b"Transfer-Encoding", b"chunked")] if chunked else []
    fake_network(Peer(wire(body, headers=headers, length=False), chunk=3))
    path = tmp_path / "chosen"
    with pytest.raises(MediaTooLarge):
        await web_media.fetch_web_media(SELECTED, path, limit=9)
    assert not path.exists()


async def test_exact_actual_limit_is_allowed_and_http_chunking_preserves_entity(
    tmp_path, fake_network
):
    body = b"3\r\nabc\r\n3\r\ndef\r\n0\r\n\r\n"
    fake_network(
        Peer(
            wire(body, headers=[(b"Transfer-Encoding", b"chunked")], length=False),
            chunk=3,
        )
    )
    path = tmp_path / "chosen"
    result = await web_media.fetch_web_media(SELECTED, path, limit=6)
    assert result.size == 6
    assert path.read_bytes() == b"abcdef"


@pytest.mark.parametrize(
    "response",
    [wire(b""), wire(b"abc", headers=[(b"Content-Length", b"8")], length=False)],
)
async def test_empty_or_truncated_content_is_not_retained(
    response, tmp_path, fake_network
):
    fake_network(Peer(response))
    path = tmp_path / "chosen"
    with pytest.raises(Refused):
        await web_media.fetch_web_media(SELECTED, path)
    assert not path.exists()


@pytest.mark.parametrize(
    "headers", [[(b"X-Padding", b"x" * (16 * 1024))], [(b"X", b"y")] * 65]
)
async def test_response_header_bytes_and_count_are_bounded(
    headers, tmp_path, fake_network
):
    fake_network(Peer(wire(headers=headers)))
    path = tmp_path / "chosen"
    with pytest.raises(Refused):
        await web_media.fetch_web_media(SELECTED, path)
    assert not path.exists()


async def test_total_timeout_bounds_stalled_transfer_and_cleans_partial(
    tmp_path, fake_network, monkeypatch
):
    peer = Peer(wire(), block=True)
    fake_network(peer)
    monkeypatch.setattr(web_media, "_TOTAL_SECONDS", 0.01)
    path = tmp_path / "chosen"
    with pytest.raises(Unavailable, match="Selected web GIF is unavailable"):
        await web_media.fetch_web_media(SELECTED, path)
    assert not path.exists()
    assert peer.closed


async def test_total_timeout_also_bounds_dns_without_connect(
    tmp_path, fake_network, monkeypatch
):
    network, _ = fake_network()

    async def stalled(host, port):
        await asyncio.Future()

    monkeypatch.setattr(web_media, "_resolve", stalled)
    monkeypatch.setattr(web_media, "_TOTAL_SECONDS", 0.01)
    path = tmp_path / "chosen"
    with pytest.raises(Unavailable):
        await web_media.fetch_web_media(SELECTED, path)
    assert network.calls == []
    assert not path.exists()


async def test_total_deadline_stops_slow_progress_even_when_each_read_succeeds(
    tmp_path, fake_network, monkeypatch
):
    class DrippingPeer(Peer):
        async def read(self, max_bytes, timeout=None):
            # Headers arrive immediately; the close-delimited entity then keeps
            # making progress, so a per-read timeout alone would never stop it.
            if self.read_count:
                await asyncio.sleep(0.005)
                self.read_count += 1
                return b"a"
            return await super().read(max_bytes, timeout)

    peer = DrippingPeer(wire(b"", length=False))
    fake_network(peer)
    monkeypatch.setattr(web_media, "_TOTAL_SECONDS", 0.02)
    path = tmp_path / "chosen"
    with pytest.raises(Unavailable):
        await web_media.fetch_web_media(SELECTED, path)
    assert peer.read_count > 1
    assert not path.exists()
    assert peer.closed


@pytest.mark.parametrize(
    "error",
    [
        httpcore.ReadTimeout("private timeout"),
        httpcore.ReadError("private transport diagnostic"),
        ssl.SSLCertVerificationError("private certificate diagnostic"),
    ],
)
async def test_transport_failures_have_fixed_errors_and_clean_their_partial(
    error, tmp_path, fake_network
):
    peer = Peer(b"", error=error)
    fake_network(peer)
    path = tmp_path / "chosen"
    with pytest.raises(Unavailable) as unavailable:
        await web_media.fetch_web_media(SELECTED, path)
    assert str(unavailable.value) == "Selected web GIF is unavailable"
    assert unavailable.value.__suppress_context__ is True
    assert not path.exists()
    assert peer.closed


async def test_cancel_does_not_leave_bytes_or_late_network_work(tmp_path, fake_network):
    peer = Peer(wire(), block=True)
    network, _ = fake_network(peer)
    path = tmp_path / "chosen"
    task = asyncio.create_task(web_media.fetch_web_media(SELECTED, path))
    await peer.started.wait()
    assert path.exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not path.exists()
    assert peer.closed
    assert len(network.calls) == 1


@pytest.mark.parametrize("symlink", [False, True])
async def test_existing_destination_is_preserved_without_external_work(
    symlink, tmp_path, fake_network
):
    network, resolutions = fake_network()
    existing = tmp_path / "existing"
    existing.write_bytes(b"owner data")
    path = tmp_path / "chosen" if symlink else existing
    if symlink:
        path.symlink_to(existing)
    with pytest.raises(Unavailable):
        await web_media.fetch_web_media(SELECTED, path)
    assert existing.read_bytes() == b"owner data"
    assert path.exists()
    assert network.calls == []
    assert resolutions == []


async def test_literal_public_address_bypasses_dns_but_verifies_actual_peer(
    tmp_path, fake_network
):
    peer = Peer(wire(), peer=OTHER_PUBLIC)
    network, resolutions = fake_network(peer)
    await web_media.fetch_web_media("https://8.8.8.8/exact", tmp_path / "chosen")
    assert resolutions == []
    assert network.calls[0][0] == OTHER_PUBLIC
    assert peer.tls[0][0] == OTHER_PUBLIC
    assert b"Host: 8.8.8.8" in b"".join(peer.writes)


async def test_socket_resolver_has_bounded_family_and_answer_count(monkeypatch):
    calls = []

    def resolver(*args):
        calls.append(args)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC, 443)),
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("2606:4700:4700::1111", 443, 0, 0),
            ),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    assert await web_media._resolve("gif.example", 443) == [
        PUBLIC,
        "2606:4700:4700::1111",
    ]
    assert calls == [
        ("gif.example", 443, socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: resolver(*args) * 17)
    with pytest.raises(Refused):
        await web_media._resolve("gif.example", 443)


async def test_remote_diagnostics_and_private_url_never_escape_errors_or_debug_logs(
    tmp_path, fake_network, caplog
):
    diagnostic = (
        "private remote diagnostic https://private.example/hidden?query=private"
    )
    first = Peer(
        wire(
            status=302,
            headers=[
                (b"Location", b"https://cdn.example/exact?private-secret=authored")
            ],
        )
    )
    failed = Peer(b"", error=httpcore.ReadError(diagnostic))
    fake_network(first, failed)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(Unavailable) as unavailable:
            await web_media.fetch_web_media(SELECTED, tmp_path / "chosen")
        logging.getLogger("httpcore.connection").debug(
            "unrelated request stays visible"
        )
    assert str(unavailable.value) == "Selected web GIF is unavailable"
    assert unavailable.value.__suppress_context__ is True
    assert "unrelated request stays visible" in caplog.text
    for private in (
        "private-query",
        "private-secret",
        "remote diagnostic",
        "gif.example",
        "cdn.example",
    ):
        assert private not in caplog.text


async def test_cleanup_failure_is_private_and_does_not_suppress_unrelated_logs(
    tmp_path, fake_network, monkeypatch, caplog
):
    fake_network(Peer(wire(status=400)))
    path = tmp_path / "chosen"

    def denied_unlink(self, *args, **kwargs):
        raise OSError("authored private filesystem diagnostic")

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "unlink", denied_unlink)
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(Unavailable) as unavailable:
                await web_media.fetch_web_media(SELECTED, path)
            logging.getLogger("httpcore.http11").debug("subsequent unrelated log")
    assert str(unavailable.value) == "Selected web GIF is unavailable"
    assert unavailable.value.__suppress_context__ is True
    assert "subsequent unrelated log" in caplog.text
    assert "private filesystem diagnostic" not in caplog.text
    # A filesystem denying unlink is the explicit limit of deletion guarantees;
    # restore normal permissions/operation and remove this authored empty file.
    path.unlink()


@pytest.mark.parametrize(
    "mime",
    [
        None,
        b"image/jpeg",
        b"image/png",
        b"image/webp",
        b"IMAGE/JPEG; authored=metadata",
        b"application/octet-stream",
        b"binary/octet-stream",
    ],
)
async def test_thumbnail_admits_only_poster_metadata_and_preserves_original_bytes(
    mime, tmp_path, fake_network
):
    # Deliberately opaque bytes: actual JPEG/PNG/WebP decoding and still-frame
    # derivation are the catalog-assets owner's separate admission step.
    original = b"authored poster bytes needing image proof\x00\xff"
    headers = [(b"Content-Type", mime)] if mime else []
    peer = Peer(wire(original, headers=headers))
    network, resolutions = fake_network(peer)
    path = tmp_path / "poster"
    result = await web_media.fetch_web_media(SELECTED, path, purpose="thumbnail")
    assert result.size == len(original)
    assert result.mime == (mime.split(b";", 1)[0].decode().lower() if mime else None)
    assert path.read_bytes() == original
    assert path.stat().st_mode & 0o777 == 0o600
    assert resolutions == [("gif.example", 443)]
    assert network.calls[0][0] == PUBLIC
    hostname, context, timeout = peer.tls[0]
    assert hostname == "gif.example"
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    assert timeout == 10
    request = b"".join(peer.writes)
    assert b"GET /exact.gif?private-query=authored-only HTTP/1.1" in request
    assert (
        b"Accept: image/jpeg, image/png, image/webp, application/octet-stream"
        in request
    )
    assert b"Accept-Encoding: identity" in request
    assert b"Host: gif.example" in request
    for header in (b"authorization:", b"cookie:", b"referer:", b"range:"):
        assert header not in request.lower()
    assert peer.closed


@pytest.mark.parametrize(
    "headers",
    [
        [(b"Content-Type", b"image/gif")],
        [(b"Content-Type", b"video/mp4")],
        [(b"Content-Type", b"image/svg+xml")],
        [(b"Content-Type", b"image/avif")],
        [(b"Content-Type", b"text/html")],
        [(b"Content-Type", b"application/json")],
        [(b"Content-Type", b"image/png"), (b"Content-Encoding", b"gzip")],
        [(b"Content-Type", b"image/webp"), (b"Content-Encoding", b"br")],
        [(b"Content-Type", b"image/png"), (b"Content-Type", b"image/jpeg")],
    ],
)
async def test_thumbnail_refuses_other_media_pages_encoding_and_duplicate_mime(
    headers, tmp_path, fake_network
):
    peer = Peer(wire(headers=headers))
    fake_network(peer)
    path = tmp_path / "poster"
    with pytest.raises(Refused) as refused:
        await web_media.fetch_web_media(SELECTED, path, purpose="thumbnail")
    assert type(refused.value) is Refused
    assert str(refused.value) == "Selected web thumbnail cannot be fetched safely"
    assert not path.exists()
    assert peer.closed


@pytest.mark.parametrize("mime", [b"image/jpeg", b"image/png", b"image/webp"])
@pytest.mark.parametrize("explicit", [False, True])
async def test_thumbnail_mime_never_expands_default_media_admission(
    mime, explicit, tmp_path, fake_network
):
    fake_network(Peer(wire(headers=[(b"Content-Type", mime)])))
    path = tmp_path / "media"
    arguments = {"purpose": "media"} if explicit else {}
    with pytest.raises(Refused) as refused:
        await web_media.fetch_web_media(SELECTED, path, **arguments)
    assert str(refused.value) == "Selected web GIF cannot be fetched safely"
    assert not path.exists()


@pytest.mark.parametrize(
    "limit", [None, web_media.GIF_BYTES, web_media.THUMBNAIL_BYTES]
)
async def test_thumbnail_declared_limit_is_five_mib_even_with_fifty_mib_caller_limit(
    limit, tmp_path, fake_network
):
    peer = Peer(
        wire(
            headers=[
                (b"Content-Type", b"image/png"),
                (b"Content-Length", str(5 * 1024**2 + 1).encode()),
            ],
            length=False,
        )
    )
    fake_network(peer)
    path = tmp_path / "poster"
    arguments = {"limit": limit} if limit is not None else {}
    with pytest.raises(MediaTooLarge) as refused:
        await web_media.fetch_web_media(
            SELECTED, path, purpose="thumbnail", **arguments
        )
    assert web_media.THUMBNAIL_BYTES == 5 * 1024**2
    assert str(refused.value) == "Selected web thumbnail exceeds the file limit"
    assert peer.read_count == 1
    assert peer.closed
    assert not path.exists()


@pytest.mark.parametrize("chunked", [False, True])
async def test_thumbnail_actual_five_mib_cap_applies_without_declared_size(
    chunked, tmp_path, fake_network
):
    original = b"x" * (5 * 1024**2 + 1)
    body = (
        (f"{len(original):x}\r\n".encode() + original + b"\r\n0\r\n\r\n")
        if chunked
        else original
    )
    headers = [(b"Transfer-Encoding", b"chunked")] if chunked else []
    peer = Peer(wire(body, headers=headers, length=False))
    fake_network(peer)
    path = tmp_path / "poster"
    with pytest.raises(MediaTooLarge) as refused:
        await web_media.fetch_web_media(
            SELECTED, path, limit=web_media.GIF_BYTES, purpose="thumbnail"
        )
    assert str(refused.value) == "Selected web thumbnail exceeds the file limit"
    assert not path.exists()
    assert peer.closed


async def test_thumbnail_exact_five_mib_is_retained_unchanged(tmp_path, fake_network):
    original = b"x" * (5 * 1024**2)
    fake_network(Peer(wire(original, headers=[(b"Content-Type", b"image/jpeg")])))
    path = tmp_path / "poster"
    result = await web_media.fetch_web_media(SELECTED, path, purpose="thumbnail")
    assert result == web_media.WebMediaFile(5 * 1024**2, "image/jpeg")
    assert path.read_bytes() == original


async def test_default_media_transfer_keeps_its_fifty_mib_cap(tmp_path, fake_network):
    original = b"x" * (5 * 1024**2 + 1)
    fake_network(Peer(wire(original)))
    path = tmp_path / "media"
    result = await web_media.fetch_web_media(SELECTED, path)
    assert result.size == len(original)
    assert path.read_bytes() == original


async def test_thumbnail_respects_a_smaller_caller_limit(tmp_path, fake_network):
    fake_network(Peer(wire(b"abcdef", length=False)))
    path = tmp_path / "poster"
    with pytest.raises(MediaTooLarge):
        await web_media.fetch_web_media(SELECTED, path, limit=5, purpose="thumbnail")
    assert not path.exists()


@pytest.mark.parametrize("purpose", [None, "poster", "video", "", False, ["thumbnail"]])
async def test_unknown_private_purpose_refuses_without_file_or_network_work(
    purpose, tmp_path, fake_network
):
    network, resolutions = fake_network()
    path = tmp_path / "poster"
    with pytest.raises(Refused) as refused:
        await web_media.fetch_web_media(SELECTED, path, purpose=purpose)
    assert str(refused.value) == "Selected web GIF cannot be fetched safely"
    assert not path.exists()
    assert network.calls == []
    assert resolutions == []


async def test_thumbnail_retains_public_pin_and_redirect_refusal(
    tmp_path, fake_network
):
    peer = Peer(wire(status=302, headers=[(b"Location", b"https://127.0.0.1/private")]))
    network, _ = fake_network(peer)
    path = tmp_path / "poster"
    with pytest.raises(Refused) as refused:
        await web_media.fetch_web_media(SELECTED, path, purpose="thumbnail")
    assert str(refused.value) == "Selected web thumbnail cannot be fetched safely"
    assert [call[0] for call in network.calls] == [PUBLIC]
    assert peer.tls[0][0] == "gif.example"
    assert peer.closed
    assert not path.exists()


async def test_thumbnail_cancel_cleans_its_partial_and_preserves_existing_path(
    tmp_path, fake_network
):
    peer = Peer(wire(), block=True)
    network, resolutions = fake_network(peer)
    path = tmp_path / "poster"
    task = asyncio.create_task(
        web_media.fetch_web_media(SELECTED, path, purpose="thumbnail")
    )
    await peer.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not path.exists()
    assert peer.closed
    assert len(network.calls) == 1
    path.write_bytes(b"owner bytes")
    with pytest.raises(Unavailable) as unavailable:
        await web_media.fetch_web_media(SELECTED, path, purpose="thumbnail")
    assert str(unavailable.value) == "Selected web thumbnail is unavailable"
    assert path.read_bytes() == b"owner bytes"
    assert len(network.calls) == 1
    assert resolutions == [("gif.example", 443)]


async def test_thumbnail_transport_diagnostics_stay_private_in_errors_and_logs(
    tmp_path, fake_network, caplog
):
    peer = Peer(
        b"", error=httpcore.ReadError("private thumbnail diagnostic query=authored")
    )
    fake_network(peer)
    path = tmp_path / "poster"
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(Unavailable) as unavailable:
            await web_media.fetch_web_media(SELECTED, path, purpose="thumbnail")
        logging.getLogger("httpcore.http11").debug("unrelated thumbnail-context log")
    assert str(unavailable.value) == "Selected web thumbnail is unavailable"
    assert unavailable.value.__suppress_context__ is True
    assert "unrelated thumbnail-context log" in caplog.text
    for private in ("private thumbnail diagnostic", "private-query", "gif.example"):
        assert private not in caplog.text
    assert not path.exists()
