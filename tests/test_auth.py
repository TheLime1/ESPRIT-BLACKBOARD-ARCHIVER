import httpx
import pytest

from bb_archive.auth import CookiePayloadError, build_httpx_cookies, cookies_from_payload


def test_loads_duplicate_cookie_names_with_domains():
    cookies = cookies_from_payload(
        {
            "bbCookies": [
                {"name": "BbRouter", "value": "a", "domain": ".blackboard.com", "path": "/"},
                {"name": "BbRouter", "value": "b", "domain": "esprit.blackboard.com", "path": "/"},
            ]
        }
    )

    jar = build_httpx_cookies(cookies)
    request = httpx.Request("GET", "https://esprit.blackboard.com/learn/api/public/v1/users/me")
    cookie_header = jar.set_cookie_header(request) or request.headers.get("cookie", "")

    assert "BbRouter" in cookie_header


def test_rejects_empty_cookie_payload():
    with pytest.raises(CookiePayloadError):
        cookies_from_payload({"bbCookies": []})
