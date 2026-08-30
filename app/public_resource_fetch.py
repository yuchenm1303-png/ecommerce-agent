from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit


class PublicResourceFetchError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class PublicResourceResponse:
    requested_url: str
    final_url: str
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    def text(self) -> str:
        content_type = self.content_type
        charset = ""
        for item in content_type.split(";")[1:]:
            name, sep, value = item.strip().partition("=")
            if sep and name.casefold() == "charset":
                charset = value.strip().strip('"\'')
                break
        for encoding in (charset, "utf-8", "gb18030", "latin-1"):
            if not encoding:
                continue
            try:
                return self.body.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
        return self.body.decode("utf-8", errors="replace")


_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _public_addresses(host: str, port: int) -> tuple[str, ...]:
    normalized = str(host or "").strip().rstrip(".")
    if not normalized:
        raise PublicResourceFetchError("background resource URL has no hostname")
    if normalized.casefold() == "localhost" or normalized.casefold().endswith(".localhost"):
        raise PublicResourceFetchError("background resource targets localhost")

    try:
        literal = ipaddress.ip_address(normalized)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise PublicResourceFetchError(
                f"background resource targets non-public address {literal.compressed}"
            )
        return (literal.compressed,)

    try:
        records = socket.getaddrinfo(
            normalized,
            int(port),
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError as exc:
        raise PublicResourceFetchError(
            f"background resource hostname could not be resolved: {normalized}"
        ) from exc

    addresses: list[str] = []
    for record in records:
        raw = str(record[4][0] or "").split("%", 1)[0]
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if not address.is_global:
            raise PublicResourceFetchError(
                f"background resource hostname {normalized} resolved to non-public address {address.compressed}"
            )
        text = address.compressed
        if text not in addresses:
            addresses.append(text)
    if not addresses:
        raise PublicResourceFetchError(
            f"background resource hostname has no usable public address: {normalized}"
        )
    return tuple(addresses)


def validate_public_resource_url(value: str) -> str:
    url = str(value or "").strip()
    parsed = urlsplit(url)
    scheme = parsed.scheme.casefold()
    if scheme not in _DEFAULT_PORTS or not parsed.hostname:
        raise PublicResourceFetchError("background resource URL must be complete http(s)")
    if parsed.username is not None or parsed.password is not None:
        raise PublicResourceFetchError("background resource URL must not contain userinfo")
    try:
        port = parsed.port or _DEFAULT_PORTS[scheme]
    except ValueError as exc:
        raise PublicResourceFetchError("background resource URL has invalid port") from exc
    _public_addresses(parsed.hostname, port)
    return url


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, *, connect_ip: str, timeout: float) -> None:
        self._connect_ip = connect_ip
        super().__init__(host, port=port, timeout=timeout)

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._connect_ip, self.port),
            self.timeout,
            self.source_address,
        )
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, *, connect_ip: str, timeout: float) -> None:
        self._connect_ip = connect_ip
        super().__init__(
            host,
            port=port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )

    def connect(self) -> None:
        raw = socket.create_connection(
            (self._connect_ip, self.port),
            self.timeout,
            self.source_address,
        )
        if self._tunnel_host:
            self.sock = raw
            self._tunnel()
            raw = self.sock
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _cookie_header(cookies: list[dict[str, object]] | tuple[dict[str, object], ...]) -> str:
    values: list[str] = []
    for item in cookies:
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "")
        if name:
            values.append(f"{name}={value}")
    return "; ".join(values)


def fetch_public_resource(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float = 15.0,
    cookies: list[dict[str, object]] | tuple[dict[str, object], ...] = (),
    referer: str = "",
    max_redirects: int = 5,
) -> PublicResourceResponse:
    """Fetch a page-discovered public resource with a pinned, bounded transport.

    This is intentionally not used for the user-supplied primary product page.
    It protects only automatic secondary fetches discovered inside that page:
    every redirect target is resolved and required to be globally routable, the
    TCP connection is pinned to that already-validated address, and the response
    body is capped before it can be materialized without bound.
    """

    limit = int(max_bytes)
    if limit < 1:
        raise ValueError("max_bytes must be positive")
    current = validate_public_resource_url(url)
    original = current

    for redirect_index in range(max(0, int(max_redirects)) + 1):
        parsed = urlsplit(current)
        scheme = parsed.scheme.casefold()
        host = str(parsed.hostname or "")
        port = parsed.port or _DEFAULT_PORTS[scheme]
        addresses = _public_addresses(host, port)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query

        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Connection": "close",
            "User-Agent": "Mozilla/5.0 EcommerceAgent/1.0",
        }
        cookie = _cookie_header(cookies)
        if cookie:
            headers["Cookie"] = cookie
        if referer:
            headers["Referer"] = str(referer)

        last_connection_error: BaseException | None = None
        response: http.client.HTTPResponse | None = None
        connection: http.client.HTTPConnection | None = None
        for address in addresses:
            try:
                if scheme == "https":
                    connection = _PinnedHTTPSConnection(
                        host,
                        port,
                        connect_ip=address,
                        timeout=float(timeout_seconds),
                    )
                else:
                    connection = _PinnedHTTPConnection(
                        host,
                        port,
                        connect_ip=address,
                        timeout=float(timeout_seconds),
                    )
                connection.request("GET", target, headers=headers)
                response = connection.getresponse()
                break
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                last_connection_error = exc
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
                connection = None
                response = None

        if response is None or connection is None:
            raise PublicResourceFetchError(
                f"background resource connection failed: {current}"
            ) from last_connection_error

        try:
            raw_headers = {
                str(key).casefold(): str(value)
                for key, value in response.getheaders()
            }
            if response.status in _REDIRECT_STATUSES:
                location = raw_headers.get("location", "").strip()
                if not location:
                    raise PublicResourceFetchError(
                        f"background resource redirect had no Location: HTTP {response.status}"
                    )
                if redirect_index >= int(max_redirects):
                    raise PublicResourceFetchError("background resource exceeded redirect limit")
                current = validate_public_resource_url(urljoin(current, location))
                continue

            if not 200 <= int(response.status) < 300:
                raise PublicResourceFetchError(
                    f"background resource returned HTTP {response.status}"
                )

            content_length = raw_headers.get("content-length", "").strip()
            if content_length:
                try:
                    announced = int(content_length)
                except ValueError:
                    announced = -1
                if announced > limit:
                    raise PublicResourceFetchError(
                        f"background resource exceeds {limit} byte budget"
                    )

            body = bytearray()
            while True:
                remaining = limit + 1 - len(body)
                if remaining <= 0:
                    raise PublicResourceFetchError(
                        f"background resource exceeds {limit} byte budget"
                    )
                chunk = response.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > limit:
                    raise PublicResourceFetchError(
                        f"background resource exceeds {limit} byte budget"
                    )
            return PublicResourceResponse(
                requested_url=original,
                final_url=current,
                status=int(response.status),
                headers=raw_headers,
                body=bytes(body),
            )
        finally:
            try:
                response.close()
            except Exception:
                pass
            try:
                connection.close()
            except Exception:
                pass

    raise PublicResourceFetchError("background resource redirect loop")


__all__ = [
    "PublicResourceFetchError",
    "PublicResourceResponse",
    "fetch_public_resource",
    "validate_public_resource_url",
]
