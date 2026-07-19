"""URL 安全驗證模組（Windows GUI 端與 macOS 端共用，對應 SDD §11）。

只接受公開 HTTP(S) 網址；拒絕帳密、localhost、loopback、private、link-local
與無 hostname 的網址；記錄 log 時遮罩 query value，避免機密外洩到公開 artifact／log。
"""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger("native_capture.url_validation")

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class UrlValidationError(ValueError):
    """URL 未通過安全驗證時拋出。"""


def _is_disallowed_ip(hostname: str) -> bool:
    """判斷 hostname 是否為 IP 字面量且屬於 loopback／private／link-local／保留位址。"""
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_url(url: str) -> str:
    """驗證公開 HTTP(S) 網址，通過則回傳原始字串；不通過拋出 UrlValidationError。

    拒絕項目：非 http/https scheme、username/password、無 hostname、
    localhost、loopback、private、link-local IP、`.local` mDNS 網域。
    """
    if not url or not isinstance(url, str):
        raise UrlValidationError("URL 為空或型別不正確")

    parts = urlsplit(url.strip())

    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UrlValidationError(f"scheme 不允許：{parts.scheme or '(空)'}（只接受 http/https）")

    if parts.username is not None or parts.password is not None:
        raise UrlValidationError("URL 不得包含 username/password")

    hostname = parts.hostname
    if not hostname:
        raise UrlValidationError("URL 缺少 hostname")

    hostname_lower = hostname.lower()
    if hostname_lower == "localhost" or hostname_lower.endswith(".localhost"):
        raise UrlValidationError("拒絕 localhost")
    if hostname_lower.endswith(".local"):
        raise UrlValidationError("拒絕 .local mDNS 網域（視為 link-local）")

    if _is_disallowed_ip(hostname):
        raise UrlValidationError(f"拒絕 loopback／private／link-local IP：{hostname}")

    return url


def mask_url_for_log(url: str) -> str:
    """回傳可安全寫入 log／artifact 的 URL：移除 userinfo，query value 全遮罩成 ***。"""
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"

    masked_query = ""
    if parts.query:
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        masked_query = urlencode([(key, "***") for key, _ in pairs])

    return urlunsplit((parts.scheme, netloc, parts.path, masked_query, ""))
