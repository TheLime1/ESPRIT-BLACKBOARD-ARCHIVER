import asyncio
import json

import httpx

from bb_archive.auth import BlackboardCookie
from bb_archive.client import AsyncBlackboardClient


def test_paginated_get_uses_next_page():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "offset=100" in str(request.url):
            return httpx.Response(200, json={"results": [{"id": "b"}]})
        return httpx.Response(
            200,
            json={
                "results": [{"id": "a"}],
                "paging": {"nextPage": "/learn/api/public/v1/users/u/courses?offset=100&limit=100"},
            },
        )

    async def run():
        transport = httpx.MockTransport(handler)
        async with AsyncBlackboardClient(
            [BlackboardCookie("BbRouter", "x")],
            transport=transport,
        ) as client:
            return await client.get_paginated("/users/u/courses")

    results = asyncio.run(run())
    assert results == [{"id": "a"}, {"id": "b"}]
    assert len(calls) == 2


def test_retries_transient_statuses():
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="try again")
        return httpx.Response(200, json={"id": "user"})

    async def run():
        transport = httpx.MockTransport(handler)
        async with AsyncBlackboardClient([BlackboardCookie("BbRouter", "x")], transport=transport) as client:
            return await client.get_json("/users/me")

    assert asyncio.run(run()) == {"id": "user"}
    assert attempts == 2
