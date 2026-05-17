from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import httpx


class CookiePayloadError(ValueError):
    """Raised when a cookie payload cannot be converted into a session."""


@dataclass(frozen=True)
class BlackboardCookie:
    name: str
    value: str
    domain: str = ""
    path: str = "/"


def _coerce_cookie(raw: Any) -> BlackboardCookie:
    if not isinstance(raw, dict):
        raise CookiePayloadError("Each cookie must be an object")

    name = str(raw.get("name") or "").strip()
    value = str(raw.get("value") or "")
    if not name or not value:
        raise CookiePayloadError("Cookie objects require non-empty name and value")

    domain = str(raw.get("domain") or "").strip()
    path = str(raw.get("path") or "/").strip() or "/"
    return BlackboardCookie(name=name, value=value, domain=domain, path=path)


def cookies_from_payload(payload: Any) -> list[BlackboardCookie]:
    """Load cookies from a dispatch payload or direct cookie list."""

    raw_cookies: Any
    if isinstance(payload, dict):
        raw_cookies = payload.get("bbCookies") or payload.get("cookies")
    else:
        raw_cookies = payload

    if not isinstance(raw_cookies, list) or not raw_cookies:
        raise CookiePayloadError("Payload must contain a non-empty bbCookies list")

    return [_coerce_cookie(cookie) for cookie in raw_cookies]


def build_httpx_cookies(cookies: Iterable[BlackboardCookie]) -> httpx.Cookies:
    jar = httpx.Cookies()
    for cookie in cookies:
        if cookie.domain:
            jar.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)
        else:
            jar.set(cookie.name, cookie.value, path=cookie.path)
    return jar
