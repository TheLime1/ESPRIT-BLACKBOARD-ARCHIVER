from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from enum import StrEnum
from pathlib import Path
from typing import Any

from bb_archive.class_codes import class_family, clean_course_name, parse_course_class
from bb_archive.client import AsyncBlackboardClient, BlackboardApiError
from bb_archive.html import extract_embedded_files, extract_image_files, is_image_file, process_html_body, wrap_html_page
from bb_archive.storage import ArchiveWriter, utc_now_iso


class ArchiveMode(StrEnum):
    HTML = "html"
    ATTACHMENTS = "attachments"
    ALL = "all"


class BlackboardArchiver:
    def __init__(
        self,
        client: AsyncBlackboardClient,
        writer: ArchiveWriter,
        *,
        class_code: str,
        mode: ArchiveMode = ArchiveMode.HTML,
        download_concurrency: int = 6,
    ) -> None:
        self.client = client
        self.writer = writer
        self.class_code = class_code.upper()
        self.family = class_family(self.class_code)
        self.mode = mode
        self._download_semaphore = asyncio.Semaphore(download_concurrency)
        self.stats: dict[str, int] = {
            "courses": 0,
            "contents": 0,
            "html_pages": 0,
            "images": 0,
            "attachments": 0,
            "skipped": 0,
            "errors": 0,
        }
        self.pages_by_course: dict[str, list[dict[str, Any]]] = defaultdict(list)

    @property
    def wants_html(self) -> bool:
        return self.mode in {ArchiveMode.HTML, ArchiveMode.ALL}

    @property
    def wants_attachments(self) -> bool:
        return self.mode in {ArchiveMode.ATTACHMENTS, ArchiveMode.ALL}

    async def run(self) -> dict[str, Any]:
        await self.client.validate()
        courses = await self.client.get_enrolled_courses()
        selected_courses = self._filter_courses(courses)
        self.stats["courses"] = len(selected_courses)

        await asyncio.gather(*(self._archive_course(course) for course in selected_courses))

        generated_at = utc_now_iso()
        manifest = {
            "schemaVersion": 1,
            "generatedAt": generated_at,
            "classCode": self.class_code,
            "family": self.family,
            "mode": str(self.mode),
            "stats": self.stats,
            "courses": [
                {
                    "id": course.get("id"),
                    "courseId": course.get("courseId"),
                    "name": clean_course_name(course.get("name")),
                    "classCode": parse_course_class(course.get("name")),
                    "url": course.get("externalAccessUrl"),
                    "pages": sorted(
                        self.pages_by_course.get(str(course.get("id") or ""), []),
                        key=lambda item: item["titlePath"],
                    ),
                }
                for course in selected_courses
            ],
        }
        self.writer.add_page_navigation(manifest)
        self.writer.write_run_manifest(manifest)
        self.writer.update_classes_index(stats=self.stats, generated_at=generated_at)
        return manifest

    def _filter_courses(self, courses: list[dict[str, Any]]) -> list[dict[str, Any]]:
        matching = []
        for course in courses:
            parsed = parse_course_class(course.get("name"))
            if parsed and class_family(parsed) == self.family:
                matching.append(course)
        return matching

    async def _archive_course(self, course: dict[str, Any]) -> None:
        course_id = str(course.get("id") or "")
        if not course_id:
            self.stats["skipped"] += 1
            return
        try:
            contents = await self.client.get_course_contents(course_id)
            await asyncio.gather(
                *(
                    self._archive_content(
                        course,
                        content,
                        [self._display_title(content)],
                    )
                    for content in contents
                )
            )
        except Exception:
            self.stats["errors"] += 1

    def _display_title(self, content: dict[str, Any], parent_title: str | None = None) -> str:
        title = str(content.get("title") or "").strip()
        if title and title.lower() != "ultradocumentbody":
            return title
        return parent_title or "Untitled"

    async def _archive_content(self, course: dict[str, Any], content: dict[str, Any], title_path: list[str]) -> None:
        self.stats["contents"] += 1
        body = content.get("body")
        content_id = str(content.get("id") or "")
        course_id = str(course.get("id") or "")
        course_name = clean_course_name(course.get("name"))

        tasks: list[asyncio.Task[None]] = []

        if self.wants_html and body:
            tasks.append(asyncio.create_task(self._write_html(course_name, course_id, title_path, content_id, str(body))))

        if self.wants_attachments:
            tasks.append(asyncio.create_task(self._download_api_attachments(course_name, course_id, title_path, content_id)))
            if body:
                tasks.append(asyncio.create_task(self._download_embedded_files(course_name, course_id, title_path, content_id, str(body))))

        if content.get("hasChildren"):
            tasks.append(asyncio.create_task(self._archive_children(course, content, title_path)))

        if tasks:
            await asyncio.gather(*tasks)

    async def _archive_children(self, course: dict[str, Any], content: dict[str, Any], title_path: list[str]) -> None:
        course_id = str(course.get("id") or "")
        content_id = str(content.get("id") or "")
        try:
            children = await self.client.get_content_children(course_id, content_id)
            await asyncio.gather(
                *(
                    self._archive_content(
                        course,
                        child,
                        [*title_path, self._display_title(child, title_path[-1] if title_path else None)],
                    )
                    for child in children
                )
            )
        except BlackboardApiError:
            self.stats["errors"] += 1

    async def _write_html(
        self,
        course_name: str,
        course_id: str,
        title_path: list[str],
        content_id: str,
        body: str,
    ) -> None:
        path = self.writer.content_path(course_name, course_id, title_path, content_id)
        asset_urls = await self._download_html_images(course_name, course_id, content_id, body, path)
        processed = process_html_body(body, self.client.domain, asset_urls)
        page = wrap_html_page(title_path[-1] if title_path else "Untitled", processed)
        self.writer.write_text(path, page)
        self.pages_by_course[course_id].append(
            {
                "title": title_path[-1] if title_path else "Untitled",
                "titlePath": title_path,
                "path": self.writer.relative_url(path),
            }
        )
        self.stats["html_pages"] += 1

    async def _download_html_images(
        self,
        course_name: str,
        course_id: str,
        content_id: str,
        body: str,
        page_path: Path,
    ) -> dict[str, str]:
        images = extract_image_files(body, self.client.domain)
        if not images:
            return {}

        async def download_image(image: Any) -> tuple[Any, Path, bool]:
            image_path = self.writer.image_path(course_name, course_id, image.filename, content_id)
            downloaded = await self._download_to_path(image.url, image_path, image.fallback_url, stat_key="images")
            return image, image_path, downloaded

        downloaded_images = await asyncio.gather(*(download_image(image) for image in images))
        asset_urls: dict[str, str] = {}
        for image, image_path, downloaded in downloaded_images:
            if not downloaded:
                continue
            relative = os.path.relpath(image_path, start=page_path.parent).replace("\\", "/")
            asset_urls[image.url] = relative
            if image.fallback_url:
                asset_urls[image.fallback_url] = relative
        return asset_urls

    async def _download_api_attachments(
        self,
        course_name: str,
        course_id: str,
        title_path: list[str],
        content_id: str,
    ) -> None:
        try:
            attachments = await self.client.get_attachments(course_id, content_id)
        except BlackboardApiError:
            self.stats["errors"] += 1
            return

        await asyncio.gather(
            *(
                self._download_attachment_endpoint(course_name, course_id, title_path, content_id, attachment)
                for attachment in attachments
            )
        )

    async def _download_attachment_endpoint(
        self,
        course_name: str,
        course_id: str,
        title_path: list[str],
        content_id: str,
        attachment: dict[str, Any],
    ) -> None:
        filename = str(attachment.get("fileName") or attachment.get("name") or attachment.get("id") or "attachment")
        if is_image_file(filename, attachment.get("mimeType")):
            self.stats["skipped"] += 1
            return

        attachment_id = str(attachment.get("id") or "")
        if not attachment_id:
            self.stats["skipped"] += 1
            return
        endpoint = f"/learn/api/public/v1/courses/{course_id}/contents/{content_id}/attachments/{attachment_id}/download"
        await self._download_to_path(endpoint, self.writer.attachment_path(course_name, course_id, title_path, filename, content_id))

    async def _download_embedded_files(
        self,
        course_name: str,
        course_id: str,
        title_path: list[str],
        content_id: str,
        body: str,
    ) -> None:
        files = extract_embedded_files(body, self.client.domain)
        await asyncio.gather(
            *(
                self._download_to_path(
                    item.url,
                    self.writer.attachment_path(course_name, course_id, title_path, item.filename, content_id),
                    item.fallback_url,
                )
                for item in files
            )
        )

    async def _download_to_path(
        self,
        url: str,
        path: Path,
        fallback_url: str | None = None,
        *,
        stat_key: str = "attachments",
    ) -> bool:
        async with self._download_semaphore:
            try:
                data, _content_type = await self.client.download_url(url)
            except BlackboardApiError:
                if not fallback_url:
                    self.stats["errors"] += 1
                    return False
                try:
                    data, _content_type = await self.client.download_url(fallback_url)
                except BlackboardApiError:
                    self.stats["errors"] += 1
                    return False
            self.writer.write_bytes(path, data)
            self.stats[stat_key] += 1
            return True
