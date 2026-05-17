# ESPRIT Blackboard Archiver

Async static archiver for ESPRIT Blackboard data. The repo is designed to run from
GitHub Actions after `esprit-portal-v2` sends a `repository_dispatch` event with a
short-lived Blackboard cookie payload.

## Modes

- `html` saves Blackboard content pages and downloads referenced images locally. This is the default.
- `attachments` downloads non-image files such as PDF, PPT, PPTX, DOCX, and ZIP.
- `all` runs both modes.

## Local Usage

Create a local payload file that matches the dispatch contract, then run:

```powershell
python -m pip install -e ".[test]"
python -m bb_archive scrape --payload-file payload.json --output-dir public
npm install
npm run build
npm run preview
```

Payload shape:

```json
{
  "classCode": "4SAE11",
  "mode": "html",
  "bbCookies": [
    { "name": "BbRouter", "value": "...", "domain": ".blackboard.com", "path": "/" }
  ],
  "source": "esprit-portal-v2",
  "requestedAt": "2026-05-17T00:00:00.000Z",
  "requestId": "uuid"
}
```

Do not commit payload files, cookies, account credentials, generated downloads, or logs.

## GitHub Action

The workflow listens for:

```json
{
  "event_type": "bb_archive",
  "client_payload": { "...": "see payload above" }
}
```

It scrapes into a Pages workspace, updates `classes.json`, publishes to the
`gh-pages` branch as a Vite-built static viewer, and optionally sends a Discord
webhook notification.
