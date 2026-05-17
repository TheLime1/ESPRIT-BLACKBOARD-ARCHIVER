# BB Archive Tasks - Archiver Repo

## Contract With `esprit-portal-v2`

- Accept only a GitHub `repository_dispatch` event named `bb_archive`.
- Required client payload fields: `classCode`, `bbCookies`.
- Optional client payload fields: `mode`, `source`, `requestedAt`, `requestId`.
- Supported modes: `html`, `attachments`, `all`. Default to `html`.
- Never require or log student passwords. The portal must not send passwords or student IDs to this repo.

## Fork vs `bbpy` Comparison

- `bbpy` in `esprit-blackboard` has the stronger REST auth model: cookie lists preserve duplicate names/domains, `/users/me` validates sessions, and v1/v2 paginated helpers follow Blackboard paging.
- This fork had more scraping-specific knowledge: content tree traversal, HTML body cleanup, `data-bbfile` parsing, stable attachment hrefs with resource URL fallback, and image-vs-document filtering.
- `bbpy` also supports Selenium login and `.id` credential caching, but those are intentionally not brought here because GitHub Actions must be non-interactive and must not store credentials.
- The revamp uses the best shared subset: portal-owned login/cookie capture, cookie-only REST auth in this repo, async `httpx` requests, Blackboard swagger as endpoint source of truth, and the fork's course content/attachment traversal behavior.

## Scraper Responsibilities

- Build an async Blackboard REST session from the provided cookies.
- Validate the session with `/learn/api/public/v1/users/me`.
- Discover enrolled courses from `/users/{userId}/courses`, then course details from `/courses/{courseId}`.
- Filter courses to the requested class family when course names include `__CLASS`.
- Crawl `/courses/{courseId}/contents` and `/contents/{contentId}/children` recursively.
- In `html` mode, write static HTML pages, download referenced images locally, and rewrite image URLs to static files.
- In `attachments` mode, download non-image API attachments and non-image `data-bbfile` links.
- In `all` mode, run both HTML and attachment flows.
- Update `classes.json` with class family, class codes, modes, latest run metadata, and counters.

## Published Static Shape

- `classes.json`: global class-family index for duplicate prevention by the portal.
- `classes/{family}/{classCode}/index.json`: run manifest for the latest archive of that class code.
- `classes/{family}/{classCode}/courses/...`: generated HTML and optional attachments.
- `index.html` and `assets/...`: Vite-built static viewer copied around the generated archive data.

## Duplicate Rule

Normalize class codes by uppercasing and stripping trailing section digits:

- `1A1`, `1A45` -> `1A`
- `1B5` -> `1B`
- `4SAE11`, `4SAE5` -> `4SAE`
- `4BI1` -> `4BI`

The portal should skip dispatch if the family already exists in deployed `classes.json`.

## Required GitHub Secrets

- `DISCORD_WEBHOOK_URL`: optional Discord notification target.
- `GITHUB_TOKEN`: automatically provided by GitHub Actions for pushing `gh-pages`.

## Verification

- Run `python -m compileall src tests`.
- Run `python -m pytest`.
- Optional live smoke test: use local credentials only, validate cookies, scrape one small class in `html` mode, then delete local payload/output/logs before committing.
