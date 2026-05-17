from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from bb_archive.auth import cookies_from_payload
from bb_archive.client import AsyncBlackboardClient
from bb_archive.scraper import ArchiveMode, BlackboardArchiver
from bb_archive.storage import ArchiveWriter


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Async ESPRIT Blackboard archiver")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scrape = subparsers.add_parser("scrape", help="Run a Blackboard archive scrape")
    scrape.add_argument("--payload-file", type=Path, required=True)
    scrape.add_argument("--output-dir", type=Path, default=Path("public"))
    scrape.add_argument("--domain", default="https://esprit.blackboard.com")
    scrape.add_argument("--mode", choices=[m.value for m in ArchiveMode], default=None)
    scrape.add_argument("--class-code", default=None)
    scrape.add_argument("--api-concurrency", type=int, default=16)
    scrape.add_argument("--download-concurrency", type=int, default=6)
    return parser


async def scrape_command(args: argparse.Namespace) -> int:
    payload = _load_json(args.payload_file)
    class_code = str(args.class_code or payload.get("classCode") or "").strip().upper()
    if not class_code:
        raise SystemExit("--class-code or payload.classCode is required")

    mode = ArchiveMode(args.mode or payload.get("mode") or ArchiveMode.HTML.value)
    cookies = cookies_from_payload(payload)

    async with AsyncBlackboardClient(
        cookies,
        domain=args.domain,
        api_concurrency=args.api_concurrency,
    ) as client:
        writer = ArchiveWriter(args.output_dir, class_code=class_code, mode=mode.value)
        archiver = BlackboardArchiver(
            client,
            writer,
            class_code=class_code,
            mode=mode,
            download_concurrency=args.download_concurrency,
        )
        manifest = await archiver.run()

    print(json.dumps({"success": True, "stats": manifest["stats"], "classCode": class_code, "mode": mode.value}))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "scrape":
        return asyncio.run(scrape_command(args))
    raise SystemExit(f"Unknown command: {args.command}")
