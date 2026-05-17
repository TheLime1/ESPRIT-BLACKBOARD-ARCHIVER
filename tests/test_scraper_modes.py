import asyncio

from bb_archive.scraper import ArchiveMode, BlackboardArchiver
from bb_archive.storage import ArchiveWriter


class FakeClient:
    domain = "https://esprit.blackboard.com"

    def __init__(self):
        self.attachments_called = 0
        self.downloads_called = 0

    async def validate(self):
        return {"id": "user", "userName": "student"}

    async def get_enrolled_courses(self):
        return [{"id": "_course_1", "courseId": "C1__4SAE11", "name": "Course__4SAE11", "externalAccessUrl": "url"}]

    async def get_course_contents(self, course_id):
        return [{"id": "_content_1", "title": "Intro", "body": "<p>Hello</p>", "hasChildren": False}]

    async def get_attachments(self, course_id, content_id):
        self.attachments_called += 1
        return [{"id": "_att_1", "fileName": "slides.pdf", "mimeType": "application/pdf"}]

    async def download_url(self, url):
        self.downloads_called += 1
        return b"asset", "application/octet-stream"


class ImageClient(FakeClient):
    async def get_course_contents(self, course_id):
        return [
            {
                "id": "_content_1",
                "title": "Intro",
                "body": '<p><img src="/bbcswebdav/image.png"></p>',
                "hasChildren": False,
            }
        ]


def test_html_mode_does_not_download_attachments(tmp_path):
    client = FakeClient()
    writer = ArchiveWriter(tmp_path, class_code="4SAE11", mode="html")
    archiver = BlackboardArchiver(client, writer, class_code="4SAE11", mode=ArchiveMode.HTML)

    manifest = asyncio.run(archiver.run())

    assert manifest["stats"]["html_pages"] == 1
    assert manifest["stats"]["images"] == 0
    assert manifest["stats"]["attachments"] == 0
    assert manifest["courses"][0]["pages"] == [
        {
            "title": "Intro",
            "titlePath": ["Intro"],
            "path": "classes/4SAE/4SAE11/courses/Course/pages/Intro-_content_1.html",
        }
    ]
    assert client.attachments_called == 0
    assert (tmp_path / "classes" / "4SAE" / "4SAE11" / "index.json").exists()


def test_html_mode_downloads_images_and_rewrites_src(tmp_path):
    client = ImageClient()
    writer = ArchiveWriter(tmp_path, class_code="4SAE11", mode="html")
    archiver = BlackboardArchiver(client, writer, class_code="4SAE11", mode=ArchiveMode.HTML)

    manifest = asyncio.run(archiver.run())

    page_path = tmp_path / manifest["courses"][0]["pages"][0]["path"]
    html = page_path.read_text(encoding="utf-8")
    assert manifest["stats"]["images"] == 1
    assert manifest["stats"]["attachments"] == 0
    assert client.attachments_called == 0
    assert "../images/_content_1/image.png" in html
    assert "archive-page-nav" in html
    assert (tmp_path / "classes" / "4SAE" / "4SAE11" / "courses" / "Course" / "images" / "_content_1" / "image.png").exists()


def test_attachments_mode_skips_html_pages(tmp_path):
    client = FakeClient()
    writer = ArchiveWriter(tmp_path, class_code="4SAE11", mode="attachments")
    archiver = BlackboardArchiver(client, writer, class_code="4SAE11", mode=ArchiveMode.ATTACHMENTS)

    manifest = asyncio.run(archiver.run())

    assert manifest["stats"]["html_pages"] == 0
    assert manifest["stats"]["attachments"] == 1
    assert client.attachments_called == 1
    assert client.downloads_called == 1


def test_course_filter_does_not_archive_unknown_suffixes(tmp_path):
    client = FakeClient()
    writer = ArchiveWriter(tmp_path, class_code="4SAE11", mode="html")
    archiver = BlackboardArchiver(client, writer, class_code="4SAE11", mode=ArchiveMode.HTML)

    selected = archiver._filter_courses(
        [
            {"id": "_course_1", "name": "No suffix"},
            {"id": "_course_2", "name": "Different__4BI1"},
            {"id": "_course_3", "name": "Match__4SAE5"},
        ]
    )

    assert selected == [{"id": "_course_3", "name": "Match__4SAE5"}]


def test_ultra_document_body_uses_parent_title(tmp_path):
    client = FakeClient()
    writer = ArchiveWriter(tmp_path, class_code="4SAE11", mode="html")
    archiver = BlackboardArchiver(client, writer, class_code="4SAE11", mode=ArchiveMode.HTML)

    assert archiver._display_title({"title": "ultraDocumentBody"}, "Week 1") == "Week 1"
