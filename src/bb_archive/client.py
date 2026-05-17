from __future__ import annotations

import asyncio
import random
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from bb_archive.auth import BlackboardCookie, build_httpx_cookies

RETRY_STATUSES = {429, 500, 502, 503, 504}


class BlackboardApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, response: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class AsyncBlackboardClient:
    def __init__(
        self,
        cookies: list[BlackboardCookie],
        *,
        domain: str = "https://esprit.blackboard.com",
        api_concurrency: int = 16,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.domain = domain.rstrip("/")
        self.api_v1 = f"{self.domain}/learn/api/public/v1"
        self.api_v2 = f"{self.domain}/learn/api/public/v2"
        self._cookies = cookies
        self._api_semaphore = asyncio.Semaphore(api_concurrency)
        self._timeout = timeout
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self.user_id: str | None = None

    async def __aenter__(self) -> "AsyncBlackboardClient":
        limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
        self._client = httpx.AsyncClient(
            cookies=build_httpx_cookies(self._cookies),
            follow_redirects=True,
            headers={"Accept": "application/json", "User-Agent": "esprit-blackboard-archiver/0.1"},
            limits=limits,
            timeout=httpx.Timeout(self._timeout),
            transport=self._transport,
        )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("AsyncBlackboardClient must be used as an async context manager")
        return self._client

    def _api_base(self, version: str) -> str:
        if version == "v2":
            return self.api_v2
        if version == "v1":
            return self.api_v1
        raise ValueError(f"Unsupported API version: {version}")

    async def _request(self, url: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        last_response: httpx.Response | None = None
        for attempt in range(5):
            async with self._api_semaphore:
                response = await self.client.get(url, params=params)
            last_response = response
            if response.status_code not in RETRY_STATUSES:
                break
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                delay = float(retry_after)
            else:
                delay = min(8.0, 0.35 * (2**attempt)) + random.uniform(0.0, 0.25)
            await asyncio.sleep(delay)

        if last_response is None:
            raise BlackboardApiError("Request did not run")
        if last_response.status_code >= 400:
            raise BlackboardApiError(
                f"Blackboard API request failed: {last_response.url}",
                status_code=last_response.status_code,
                response=last_response.text[:1000],
            )
        return last_response

    async def get_json(
        self,
        endpoint: str,
        *,
        version: str = "v1",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = endpoint if endpoint.startswith("http") else f"{self._api_base(version)}{endpoint}"
        response = await self._request(url, params=params)
        return response.json()

    async def get_paginated(
        self,
        endpoint: str,
        *,
        version: str = "v1",
        params: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        all_results: list[dict[str, Any]] = []
        current_endpoint = endpoint
        current_params: dict[str, Any] = {"limit": limit, **(params or {})}

        while True:
            data = await self.get_json(current_endpoint, version=version, params=current_params)
            results = data.get("results") or []
            if not isinstance(results, list):
                raise BlackboardApiError(f"Paginated endpoint returned non-list results: {current_endpoint}")
            all_results.extend(results)

            next_page = (data.get("paging") or {}).get("nextPage")
            if not next_page:
                break

            current_endpoint, current_params = self._parse_next_page(next_page, version)

        return all_results

    def _parse_next_page(self, next_page: str, version: str) -> tuple[str, dict[str, Any]]:
        parsed = urlsplit(next_page)
        path = parsed.path or next_page.split("?", 1)[0]
        prefix = f"/learn/api/public/{version}"
        if path.startswith(prefix):
            path = path[len(prefix) :]
        query = {
            key: values[-1]
            for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
            if values
        }
        return path, query

    async def validate(self) -> dict[str, Any]:
        user = await self.get_json("/users/me")
        self.user_id = user.get("id")
        if not self.user_id:
            raise BlackboardApiError("Authenticated user response did not include an id")
        return user

    async def get_enrolled_courses(self) -> list[dict[str, Any]]:
        if not self.user_id:
            await self.validate()
        memberships = await self.get_paginated(f"/users/{self.user_id}/courses")

        async def fetch_course(membership: dict[str, Any]) -> dict[str, Any] | None:
            course_id = membership.get("courseId")
            if not course_id:
                return None
            try:
                course = await self.get_json(f"/courses/{course_id}")
            except BlackboardApiError:
                return None
            if not course.get("externalAccessUrl"):
                return None
            course["membership"] = membership
            return course

        courses = await asyncio.gather(*(fetch_course(m) for m in memberships))
        return [course for course in courses if course]

    async def get_course_contents(self, course_id: str) -> list[dict[str, Any]]:
        return await self.get_paginated(f"/courses/{course_id}/contents")

    async def get_content_children(self, course_id: str, content_id: str) -> list[dict[str, Any]]:
        return await self.get_paginated(f"/courses/{course_id}/contents/{content_id}/children")

    async def get_attachments(self, course_id: str, content_id: str) -> list[dict[str, Any]]:
        try:
            return await self.get_paginated(f"/courses/{course_id}/contents/{content_id}/attachments")
        except BlackboardApiError as exc:
            if exc.status_code in {400, 403, 404}:
                return []
            raise

    async def download_url(self, url_or_endpoint: str) -> tuple[bytes, str | None]:
        url = url_or_endpoint
        if url.startswith("/"):
            url = f"{self.domain}{url}"
        response = await self._request(url)
        return response.content, response.headers.get("Content-Type")
