from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bb_archive.class_codes import class_family
from bb_archive.html import inject_page_navigation, sanitize_filename


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


class ArchiveWriter:
    def __init__(self, output_dir: Path, *, class_code: str, mode: str) -> None:
        self.output_dir = output_dir
        self.class_code = class_code.upper()
        self.family = class_family(self.class_code)
        self.mode = mode
        self.class_root = self.output_dir / "classes" / self.family / self.class_code

    def course_dir(self, course_name: str, course_id: str) -> Path:
        slug = sanitize_filename(course_name, default=course_id or "course", max_length=80)
        return self.class_root / "courses" / slug

    def content_path(self, course_name: str, course_id: str, title_path: list[str], content_id: str) -> Path:
        title = sanitize_filename(title_path[-1] if title_path else content_id, default="page", max_length=80)
        safe_id = sanitize_filename(content_id, default="content", max_length=40)
        return self.course_dir(course_name, course_id) / "pages" / f"{title}-{safe_id}.html"

    def attachment_path(
        self,
        course_name: str,
        course_id: str,
        title_path: list[str],
        filename: str,
        content_id: str | None = None,
    ) -> Path:
        folder_name = content_id or (title_path[-1] if title_path else "content")
        folder = sanitize_filename(folder_name, default="content", max_length=56)
        return self.course_dir(course_name, course_id) / "attachments" / folder / sanitize_filename(
            filename,
            default="attachment",
            max_length=96,
        )

    def image_path(self, course_name: str, course_id: str, filename: str, content_id: str | None = None) -> Path:
        folder = sanitize_filename(content_id, default="content", max_length=56)
        return self.course_dir(course_name, course_id) / "images" / folder / sanitize_filename(
            filename,
            default="image",
            max_length=96,
        )

    def write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def write_bytes(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def write_run_manifest(self, manifest: dict[str, Any]) -> None:
        write_json(self.class_root / "index.json", manifest)

    def relative_url(self, path: Path) -> str:
        return path.relative_to(self.output_dir).as_posix()

    def add_page_navigation(self, manifest: dict[str, Any]) -> None:
        linear_pages: list[dict[str, Any]] = []
        for course in manifest.get("courses", []):
            for page in course.get("pages", []):
                linear_pages.append({"course": course, "page": page})

        def nav_target(current_path: Path, item: dict[str, Any] | None) -> dict[str, str] | None:
            if item is None:
                return None
            target_path = self.output_dir / item["page"]["path"]
            return {
                "href": os.path.relpath(target_path, start=current_path.parent).replace("\\", "/"),
                "label": str(item["page"].get("title") or item["course"].get("name") or "Untitled"),
            }

        total = len(linear_pages)
        for index, item in enumerate(linear_pages):
            page = item["page"]
            course = item["course"]
            page_path = self.output_dir / page["path"]
            if not page_path.exists():
                continue
            current_label = f"{course.get('name', 'Course')} / {page.get('title', 'Untitled')}"
            html = page_path.read_text(encoding="utf-8")
            html = inject_page_navigation(
                html,
                current_label=current_label,
                progress_label=f"Page {index + 1} of {total}",
                previous_page=nav_target(page_path, linear_pages[index - 1] if index > 0 else None),
                next_page=nav_target(page_path, linear_pages[index + 1] if index + 1 < total else None),
            )
            page_path.write_text(html, encoding="utf-8")

    def update_classes_index(self, *, stats: dict[str, Any], generated_at: str | None = None) -> None:
        generated_at = generated_at or utc_now_iso()
        index_path = self.output_dir / "classes.json"
        index = read_json(index_path, {"schemaVersion": 1, "generatedAt": generated_at, "classes": []})
        classes = index.get("classes")
        if not isinstance(classes, list):
            classes = []

        entry = None
        for candidate in classes:
            if isinstance(candidate, dict) and candidate.get("family") == self.family:
                entry = candidate
                break

        if entry is None:
            entry = {
                "family": self.family,
                "classCodes": [],
                "modes": [],
                "latestRunAt": generated_at,
                "url": f"classes/{self.family}/{self.class_code}/index.json",
            }
            classes.append(entry)

        class_codes = sorted(set([*entry.get("classCodes", []), self.class_code]))
        modes = sorted(set([*entry.get("modes", []), self.mode]))
        entry.update(
            {
                "classCodes": class_codes,
                "modes": modes,
                "latestRunAt": generated_at,
                "latestClassCode": self.class_code,
                "courseCount": stats.get("courses", 0),
                "htmlPages": stats.get("html_pages", 0),
                "images": stats.get("images", 0),
                "attachments": stats.get("attachments", 0),
                "errors": stats.get("errors", 0),
                "url": f"classes/{self.family}/{self.class_code}/index.json",
            }
        )

        index["schemaVersion"] = 1
        index["generatedAt"] = generated_at
        index["classes"] = sorted(classes, key=lambda item: str(item.get("family", "")))
        write_json(index_path, index)
