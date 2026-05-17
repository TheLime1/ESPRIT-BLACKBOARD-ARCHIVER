from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "tiff", "tif", "avif"}

ARCHIVE_PAGE_NAV_CSS = """
.archive-page-nav {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  gap: 12px;
  align-items: stretch;
  padding: 10px 12px;
  background: #f8fafc;
  border-bottom: 1px solid #d7dee7;
  font-family: Arial, Helvetica, sans-serif;
  font-size: 14px;
  z-index: 10;
}
.archive-page-nav-top {
  position: sticky;
  top: 0;
}
.archive-page-nav-bottom {
  margin-top: 32px;
  border-top: 1px solid #d7dee7;
  border-bottom: 0;
}
.archive-page-link,
.archive-page-position {
  min-height: 48px;
  display: grid;
  align-content: center;
  gap: 3px;
  padding: 8px 10px;
  border-radius: 6px;
  box-shadow: 0 1px 2px rgba(16, 34, 53, 0.08);
}
.archive-page-link {
  appearance: none;
  color: #102235;
  background: #ffffff;
  border: 1px solid #cbd5df;
  cursor: pointer;
  text-decoration: none !important;
  transition:
    background-color 120ms ease,
    border-color 120ms ease,
    box-shadow 120ms ease,
    transform 120ms ease;
}
.archive-page-link:hover {
  border-color: #147f71;
  background: #f4fbf9;
  box-shadow: 0 3px 8px rgba(16, 34, 53, 0.12);
  text-decoration: none !important;
  transform: translateY(-1px);
}
.archive-page-link:focus-visible {
  outline: 3px solid rgba(20, 127, 113, 0.25);
  outline-offset: 2px;
}
.archive-page-next {
  text-align: right;
}
.archive-page-link span,
.archive-page-position span {
  color: #687888;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}
.archive-page-link strong,
.archive-page-position strong {
  overflow: hidden;
  color: #17212b;
  font-size: 13px;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.archive-page-position {
  min-width: 160px;
  color: #17212b;
  text-align: center;
}
.archive-page-link-disabled {
  opacity: 0.42;
  cursor: default;
  transform: none;
}
"""


@dataclass(frozen=True)
class EmbeddedFile:
    filename: str
    url: str
    fallback_url: str | None = None
    mime_type: str | None = None


def sanitize_filename(value: str | None, *, default: str = "untitled", max_length: int = 96) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", str(value or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    if len(cleaned) > max_length:
        stem, dot, suffix = cleaned.rpartition(".")
        if dot and 1 <= len(suffix) <= 12 and max_length > len(suffix) + 1:
            cleaned = f"{stem[: max_length - len(suffix) - 1].rstrip(' .-')}.{suffix}"
        else:
            cleaned = cleaned[:max_length].rstrip(" .-")
    return cleaned or default


def is_image_file(filename: str | None, mime_type: str | None = None) -> bool:
    if mime_type and mime_type.lower().startswith("image/"):
        return True
    if filename and "." in filename:
        return filename.rsplit(".", 1)[-1].lower() in IMAGE_EXTENSIONS
    return False


def absolute_url(url: str, domain: str) -> str:
    return urljoin(domain.rstrip("/") + "/", url)


def _filename_from_url(url: str, *, default: str) -> str:
    path = unquote(urlsplit(url).path)
    filename = path.rsplit("/", 1)[-1]
    return sanitize_filename(filename, default=default)


def _unique_filename(filename: str, seen: set[str]) -> str:
    candidate = filename
    index = 2
    while candidate.lower() in seen:
        stem, dot, suffix = filename.rpartition(".")
        candidate = f"{stem}-{index}.{suffix}" if dot else f"{filename}-{index}"
        index += 1
    seen.add(candidate.lower())
    return candidate


def _parse_bbfile(raw: str) -> dict[str, object] | None:
    try:
        decoded = html.unescape(raw)
        data = json.loads(decoded)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _local_asset_url(candidates: list[str], domain: str, asset_urls: dict[str, str]) -> str | None:
    for candidate in candidates:
        if not candidate:
            continue
        absolute = absolute_url(candidate, domain)
        if absolute in asset_urls:
            return asset_urls[absolute]
    return None


def _is_local_asset_reference(value: str, asset_urls: dict[str, str]) -> bool:
    return value in set(asset_urls.values())


def _replace_with_image(soup: BeautifulSoup, tag: object, src: str, meta: dict[str, object]) -> None:
    alt = str(
        meta.get("alternativeText")
        or meta.get("displayName")
        or meta.get("fileName")
        or getattr(tag, "get_text", lambda **_kwargs: "")(strip=True)
        or ""
    )
    if getattr(tag, "name", None) == "img":
        tag["src"] = src
        if alt and not tag.get("alt"):
            tag["alt"] = alt
        tag["loading"] = tag.get("loading") or "lazy"
        tag["decoding"] = tag.get("decoding") or "async"
        return

    img = soup.new_tag("img", src=src)
    if alt:
        img["alt"] = alt
    img["loading"] = "lazy"
    img["decoding"] = "async"
    tag.replace_with(img)


def process_html_body(body_html: str | None, domain: str, asset_urls: dict[str, str] | None = None) -> str:
    if not body_html:
        return ""

    asset_urls = asset_urls or {}
    soup = BeautifulSoup(str(body_html), "html.parser")

    for tag in soup.find_all(attrs={"data-bbfile": True}):
        meta = _parse_bbfile(tag.get("data-bbfile", ""))
        if not meta:
            continue
        href = tag.get("href") or ""
        src = tag.get("src") or ""
        resource_url = str(meta.get("resourceUrl") or "")
        image_like = is_image_file(
            str(meta.get("fileName") or meta.get("linkName") or href or src or resource_url),
            str(meta.get("mimeType") or ""),
        )
        local = _local_asset_url([src, href, resource_url], domain, asset_urls)
        if local:
            if image_like:
                _replace_with_image(soup, tag, local, meta)
                continue
        target = href or src or resource_url
        if target:
            resolved = absolute_url(target, domain)
            if image_like:
                _replace_with_image(soup, tag, resolved, meta)
            else:
                tag["href"] = resolved

    for img in soup.find_all("img"):
        src = img.get("src")
        if src and not src.startswith("data:"):
            if _is_local_asset_reference(src, asset_urls):
                continue
            absolute = absolute_url(src, domain)
            img["src"] = asset_urls.get(absolute, absolute)

    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if href and not href.startswith(("mailto:", "tel:", "#")):
            if _is_local_asset_reference(href, asset_urls):
                continue
            absolute = absolute_url(href, domain)
            anchor["href"] = asset_urls.get(absolute, absolute)

    return str(soup)


def extract_image_files(body_html: str | None, domain: str) -> list[EmbeddedFile]:
    if not body_html:
        return []

    soup = BeautifulSoup(str(body_html), "html.parser")
    images: list[EmbeddedFile] = []
    seen_urls: set[str] = set()
    seen_filenames: set[str] = set()

    def append_image(filename: str, url: str, fallback_url: str | None = None, mime_type: str | None = None) -> None:
        absolute = absolute_url(url, domain)
        if absolute in seen_urls or absolute.startswith("data:"):
            return
        seen_urls.add(absolute)
        safe_filename = _unique_filename(sanitize_filename(filename, default="image"), seen_filenames)
        images.append(
            EmbeddedFile(
                filename=safe_filename,
                url=absolute,
                fallback_url=absolute_url(fallback_url, domain) if fallback_url else None,
                mime_type=mime_type,
            )
        )

    for index, tag in enumerate(soup.find_all(attrs={"data-bbfile": True}), start=1):
        meta = _parse_bbfile(tag.get("data-bbfile", ""))
        if not meta:
            continue
        filename = str(meta.get("fileName") or meta.get("linkName") or "")
        mime_type = str(meta.get("mimeType") or "") or None
        href = tag.get("href") or ""
        src = tag.get("src") or ""
        resource_url = str(meta.get("resourceUrl") or "")
        primary = src or href or resource_url
        if not primary:
            continue
        if not is_image_file(filename or primary, mime_type):
            continue
        append_image(filename or _filename_from_url(primary, default=f"image-{index}"), primary, resource_url if primary != resource_url else None, mime_type)

    for index, img in enumerate(soup.find_all("img"), start=1):
        src = img.get("src") or ""
        if not src or src.startswith("data:"):
            continue
        append_image(_filename_from_url(src, default=f"image-{index}"), src)

    return images


def extract_embedded_files(body_html: str | None, domain: str) -> list[EmbeddedFile]:
    if not body_html:
        return []

    soup = BeautifulSoup(str(body_html), "html.parser")
    files: list[EmbeddedFile] = []
    seen: set[tuple[str, str]] = set()

    for tag in soup.find_all(attrs={"data-bbfile": True}):
        meta = _parse_bbfile(tag.get("data-bbfile", ""))
        if not meta:
            continue

        filename = str(meta.get("fileName") or meta.get("linkName") or tag.get_text(strip=True) or "")
        mime_type = str(meta.get("mimeType") or "") or None
        if not filename or is_image_file(filename, mime_type):
            continue

        href = tag.get("href") or ""
        resource_url = str(meta.get("resourceUrl") or "")
        primary = href or resource_url
        if not primary:
            continue

        item = EmbeddedFile(
            filename=sanitize_filename(filename),
            url=absolute_url(primary, domain),
            fallback_url=absolute_url(resource_url, domain) if href and resource_url else None,
            mime_type=mime_type,
        )
        key = (item.filename, item.url)
        if key not in seen:
            files.append(item)
            seen.add(key)

    return files


def inject_page_navigation(
    page_html: str,
    *,
    current_label: str,
    progress_label: str,
    previous_page: dict[str, str] | None = None,
    next_page: dict[str, str] | None = None,
) -> str:
    soup = BeautifulSoup(page_html, "html.parser")
    if not soup.body:
        return page_html

    for existing in soup.select(".archive-page-nav"):
        existing.decompose()

    if not soup.head:
        head = soup.new_tag("head")
        if soup.html:
            soup.html.insert(0, head)
        else:
            soup.insert(0, head)

    if soup.head:
        style = soup.head.select_one("style[data-archive-page-nav]")
        if not style:
            style = soup.new_tag("style")
            style["data-archive-page-nav"] = "true"
            soup.head.append(style)
        style.string = ARCHIVE_PAGE_NAV_CSS

    def nav_link(label: str, target: dict[str, str] | None, class_name: str) -> str:
        if not target:
            return f'<span class="archive-page-link archive-page-link-disabled {class_name}">{label}</span>'
        return (
            f'<a class="archive-page-link {class_name}" href="{html.escape(target["href"], quote=True)}">'
            f'<span>{label}</span>'
            f'<strong>{html.escape(target["label"])}</strong>'
            "</a>"
        )

    def nav_markup(extra_class: str = "") -> str:
        return f"""
<nav class="archive-page-nav {extra_class}" aria-label="Archive page navigation">
  {nav_link("Prev", previous_page, "archive-page-prev")}
  <div class="archive-page-position">
    <span>{html.escape(progress_label)}</span>
    <strong>{html.escape(current_label)}</strong>
  </div>
  {nav_link("Next", next_page, "archive-page-next")}
</nav>
"""

    soup.body.insert(0, BeautifulSoup(nav_markup("archive-page-nav-top"), "html.parser"))
    soup.body.append(BeautifulSoup(nav_markup("archive-page-nav-bottom"), "html.parser"))
    return str(soup)


def wrap_html_page(title: str, body: str) -> str:
    safe_title = html.escape(title)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    body {{
      margin: 0;
      color: #1f2933;
      background: #ffffff;
      font-family: Arial, Helvetica, sans-serif;
      line-height: 1.55;
    }}
    main {{
      max-width: 860px;
      padding: 32px 24px 64px;
    }}
    img, video, iframe {{
      max-width: 100%;
    }}
    pre {{
      overflow-x: auto;
    }}
  </style>
  <style data-archive-page-nav="true">{ARCHIVE_PAGE_NAV_CSS}</style>
</head>
<body>
  <main>
    <h1>{safe_title}</h1>
    {body}
  </main>
</body>
</html>
"""
