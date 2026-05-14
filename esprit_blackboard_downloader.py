#!/usr/bin/env python3
"""
TODO : order
link html files



#!/usr/bin/env python3
"""
ESPRIT Complete Downloader
- Logs to ./logs/download_TIMESTAMP.log
- Embeds images inline as base64 (fixes missing images in saved HTML)
- Diagnoses every URL in the body: tries to fetch it, reports status
- Faithful HTML reproduction (keeps all inline styles, fonts, colours)
- Fixes ultraDocumentBody naming (uses parent folder title)
"""

from blackboard import BlackBoardClient
from bs4 import BeautifulSoup
import json
import sys
import os
import re
import base64
import html as html_lib
import logging
import getpass
from datetime import datetime
from colorama import Fore, Style, init
from urllib.parse import unquote, urlparse

init()

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs('./logs', exist_ok=True)
log_filename = f"./logs/download_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

class StripAnsi(logging.Formatter):
    _re = re.compile(r'\x1b\[[0-9;]*m')
    def format(self, record):
        return self._re.sub('', super().format(record))

_fh = logging.FileHandler(log_filename, encoding='utf-8')
_fh.setFormatter(StripAnsi('%(asctime)s  %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
_ch = logging.StreamHandler(sys.stdout)
_ch.setFormatter(logging.Formatter('%(message)s'))
log = logging.getLogger('esprit')
log.setLevel(logging.DEBUG)
log.addHandler(_fh)
log.addHandler(_ch)
# ─────────────────────────────────────────────────────────────────────────────


# ── URL helpers ───────────────────────────────────────────────────────────────
def is_blackboard_url(url, site):
    if url.startswith('/'):
        return True
    return urlparse(url).netloc == urlparse(site).netloc

def absolute_url(url, site):
    if url.startswith('/'):
        return site.rstrip('/') + url
    return url

def fetch_as_base64(session, url, site, indent=''):
    """
    Try to GET url with the authenticated session.
    Returns (base64_data, mime_type, status_description)
    """
    full_url = absolute_url(url, site)
    try:
        r = session.get(full_url, allow_redirects=True, timeout=15)
        status = f"{r.status_code} {r.reason}"
        if r.status_code == 200:
            mime = r.headers.get('Content-Type', '').split(';')[0].strip() or 'application/octet-stream'
            b64  = base64.b64encode(r.content).decode('ascii')
            log.debug(f"{indent}  [embed] OK {r.status_code} — {full_url[:80]}")
            return b64, mime, status
        else:
            log.warning(f"{indent}  [embed] FAIL {r.status_code} — {full_url[:80]}")
            return None, None, status
    except Exception as e:
        log.error(f"{indent}  [embed] ERROR — {full_url[:80]} — {e}")
        return None, None, str(e)
# ─────────────────────────────────────────────────────────────────────────────


# ── Body post-processor ───────────────────────────────────────────────────────
def process_body(body_html, session, site, indent=''):
    """
    Walk every element in the BB body and:
      1. data-bbfile with image mimeType or render=inlineOnly -> embed as <img base64>
      2. <img src="..."> pointing to BB                       -> embed src as base64
      3. All other <a href> on BB                             -> make absolute
    Returns processed HTML string.
    """
    soup = BeautifulSoup(body_html, 'html.parser')

    # ── 1. data-bbfile inline attachments ────────────────────────────────────
    for tag in soup.find_all(attrs={'data-bbfile': True}):
        raw = tag.get('data-bbfile', '')
        try:
            meta = json.loads(html_lib.unescape(raw))
        except Exception:
            continue

        mime     = meta.get('mimeType', '')
        render   = meta.get('render', '')
        fname    = meta.get('fileName', '') or meta.get('linkName', '')
        resource = meta.get('resourceUrl', '')
        href     = tag.get('href', '')

        is_image = mime.startswith('image/')

        if is_image:
            embedded = False
            for try_url in [u for u in [resource, href] if u]:
                b64, fetched_mime, status = fetch_as_base64(session, try_url, site, indent)
                if b64:
                    actual_mime = fetched_mime or mime or 'image/png'
                    img_tag = soup.new_tag(
                        'img',
                        src=f"data:{actual_mime};base64,{b64}",
                        alt=meta.get('alternativeText', fname),
                        style="max-width:100%;height:auto;display:block;margin:8px 0;")
                    tag.replace_with(img_tag)
                    log.info(f"{indent}  {Fore.GREEN}[img embedded] {fname}{Style.RESET_ALL}")
                    embedded = True
                    break
            if not embedded:
                log.warning(f"{indent}  {Fore.YELLOW}[img FAILED — kept as link] {fname}{Style.RESET_ALL}")
                if href:
                    tag['href'] = absolute_url(href, site)
        else:
            # PDFs, docx, and everything else — keep as absolute link, never embed
            if href and not href.startswith('http'):
                tag['href'] = absolute_url(href, site)
            log.debug(f"{indent}  [non-image kept as link] {fname} ({mime})")

    # ── 2. Plain <img> tags ───────────────────────────────────────────────────
    for img in soup.find_all('img'):
        src = img.get('src', '')
        if not src or src.startswith('data:'):
            continue
        if is_blackboard_url(src, site):
            b64, fetched_mime, status = fetch_as_base64(session, src, site, indent)
            if b64:
                img['src'] = f"data:{fetched_mime};base64,{b64}"
                log.info(f"{indent}  {Fore.GREEN}[img embedded] {src[:60]}{Style.RESET_ALL}")
            else:
                log.warning(f"{indent}  {Fore.YELLOW}[img FAILED] {src[:60]} -> {status}{Style.RESET_ALL}")
        # External images: leave alone — they'll load if internet is available

    # ── 3. Make all BB hrefs absolute ────────────────────────────────────────
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.startswith(('data:', '#', 'mailto:')):
            continue
        if is_blackboard_url(href, site):
            a['href'] = absolute_url(href, site)

    return str(soup)
# ─────────────────────────────────────────────────────────────────────────────


# ── File helpers ─────────────────────────────────────────────────────────────

# Image types are embedded in HTML — skip them as standalone downloads
IMAGE_EXTS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico', 'tiff', 'tif'}

def extract_file_links_from_body(body_html):
    """Extract all non-image embedded file links from BB body HTML."""
    file_links = []
    if not body_html:
        return file_links

    body_str = str(body_html)

    # Method 1: data-bbfile JSON — explicit filename, any non-image type
    for match in re.findall(r'data-bbfile="({[^"]+})"', body_str):
        try:
            meta  = json.loads(html_lib.unescape(match))
            fname = meta.get('fileName') or meta.get('linkName', '')
            if not fname:
                continue
            ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
            if ext in IMAGE_EXTS:
                continue  # images are embedded in HTML, not downloaded separately

            # resourceUrl is a pre-signed /sessions/ link (may expire → 403)
            # href is the stable bbcswebdav token URL — use as primary, resourceUrl as fallback
            resource_url = meta.get('resourceUrl', '')

            # Find href from the anchor tag (href can appear before OR after data-bbfile)
            anchor_m = re.search(
                rf'<a\s[^>]*data-bbfile="{re.escape(match)}"[^>]*>', body_str)
            if not anchor_m:
                anchor_m = re.search(
                    rf'<a\s[^>]*href="[^"]*"[^>]*data-bbfile="{re.escape(match)}"[^>]*>',
                    body_str)
            href = ''
            if anchor_m:
                hm = re.search(r'href="([^"]+)"', anchor_m.group(0))
                if hm:
                    href = html_lib.unescape(hm.group(1))

            # Prefer href (stable token URL); fall back to resourceUrl
            url = href if href else resource_url
            fallback_url = resource_url if href else ''

            if url and not any(l['url'] == url for l in file_links):
                file_links.append({'url': url, 'fallback_url': fallback_url, 'filename': fname,
                                    'mime': meta.get('mimeType', '')})
        except Exception:
            pass

    return file_links


def download_file(client, url, filename, save_path, fallback_url=''):
    """Download any non-image file. Tries url first, then fallback_url on 403."""
    if url.startswith('/'):
        url = client.site + url
    filename_safe = unquote(re.sub(r'[<>:"/\\|?*]', '', filename))
    dest = os.path.abspath(os.path.join(save_path, filename_safe))
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    if os.path.isfile(dest):
        log.info(f"      {Fore.YELLOW}⚠ Exists: {filename_safe}{Style.RESET_ALL}")
        return False

    urls_to_try = [u for u in [url, fallback_url] if u]
    for try_url in urls_to_try:
        if try_url.startswith('/'):
            try_url = client.site + try_url
        try:
            r = client.session.get(try_url, allow_redirects=True)
            if r.status_code == 200:
                with open(dest, 'wb') as f:
                    f.write(r.content)
                log.info(f"      {Fore.GREEN}✓ Downloaded: {filename_safe}{Style.RESET_ALL}")
                return True
            else:
                log.debug(f"      {Fore.YELLOW}⚠ {r.status_code} on {try_url[:70]}{Style.RESET_ALL}")
        except Exception as e:
            log.debug(f"      {Fore.RED}✗ Error on {try_url[:70]}: {e}{Style.RESET_ALL}")

    log.warning(f"      {Fore.RED}✗ Failed: {filename_safe}{Style.RESET_ALL}")
    return False
# ─────────────────────────────────────────────────────────────────────────────


# ── HTML page saver ───────────────────────────────────────────────────────────
def save_html_page(content, save_path, client, parent_title=None):
    if not content.body:
        return False

    raw_title = (content.title or '').strip()
    if raw_title.lower() in ('', 'ultradocumentbody') and parent_title:
        display_title = parent_title
    else:
        display_title = raw_title

    filename_base = re.sub(r'[<>:"/\\|?*]', '', display_title).strip() or 'page'
    filename      = filename_base + '.html'
    dest          = os.path.abspath(os.path.join(save_path, filename))
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    if os.path.isfile(dest):
        log.info(f"      {Fore.YELLOW}⚠ Exists: {filename}{Style.RESET_ALL}")
        return False

    log.info(f"      {Fore.CYAN}Processing HTML: {filename}{Style.RESET_ALL}")

    processed_body = process_body(content.body, client.session, client.site, indent='      ')

    html_doc = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{html_lib.escape(display_title)}</title>
    <style>
        *, *::before, *::after {{ box-sizing: border-box; }}

        body {{
            font-family: Arial, Helvetica, sans-serif;
            font-size: 14px;
            line-height: 1.5;
            color: #222;
            background: #fff;
            margin: 0;
            padding: 0;
        }}

        /* ── BB Ultra page shell ── */
        .bb-page-header {{
            padding: 24px 40px 16px 40px;
            border-bottom: 2px solid #c7cdd4;
            margin-bottom: 24px;
        }}
        .bb-page-header h1 {{
            font-size: 1.75rem;
            font-weight: 600;
            color: #1a1a1a;
            margin: 0;
        }}

        /* ── Content column matches BB Ultra's narrow reading column ── */
        .bb-content {{
            max-width: 780px;
            padding: 0 40px 60px 40px;
        }}

        /* ── Respect BB's inline font-size overrides (h6 stays small) ── */
        h1 {{ font-size: 1.5rem; }}
        h2 {{ font-size: 1.3rem; }}
        h3 {{ font-size: 1.15rem; }}
        h4 {{ font-size: 1rem; }}
        h5 {{ font-size: 0.95rem; }}
        h6 {{ font-size: 0.875rem; font-weight: normal; }}
        h1,h2,h3,h4,h5,h6 {{ margin-top: 1em; margin-bottom: 0.3em; }}

        /* ── Images ── */
        img {{
            max-width: 100%;
            height: auto;
            display: block;
            margin: 12px 0;
        }}

        /* ── Links ── */
        a {{ color: #1a6fb5; text-decoration: underline; }}

        /* ── Tables ── */
        table {{ border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.9rem; }}
        th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
        th {{ background: #f4f4f4; font-weight: 600; }}

        /* ── Code ── */
        pre, code {{ font-family: "Courier New", monospace; font-size: 0.85rem;
                     background: #f5f5f5; padding: 2px 5px; border-radius: 3px; }}
        pre {{ padding: 12px; overflow-x: auto; line-height: 1.4; }}

        /* ── Blockquote ── */
        blockquote {{ border-left: 4px solid #c7cdd4; margin: 0.5em 0;
                      padding: 0.3em 1em; color: #555; }}

        /* ── Lists ── */
        ul, ol {{ padding-left: 1.5em; margin: 0.5em 0; }}
        li {{ margin-bottom: 0.2em; }}

        /* ── BB-specific wrappers — don't add extra block spacing ── */
        [data-bbid] {{ display: block; }}
        p {{ margin: 0.4em 0; }}
    </style>
</head>
<body>
    <div class="bb-page-header">
        <h1>{html_lib.escape(display_title)}</h1>
    </div>
    <div class="bb-content">
        {processed_body}
    </div>
</body>
</html>"""

    try:
        with open(dest, 'w', encoding='utf-8') as f:
            f.write(html_doc)
        log.info(f"      {Fore.GREEN}✓ Saved: {filename}{Style.RESET_ALL}")
        return True
    except Exception as e:
        log.error(f"      {Fore.RED}✗ Error saving {filename}: {e}{Style.RESET_ALL}")
        return False
# ─────────────────────────────────────────────────────────────────────────────


# ── Course downloader ─────────────────────────────────────────────────────────
def download_course_complete(course, save_location='./downloads', save_html_pages=False):
    log.info(f"\n{Fore.CYAN}{'=' * 70}{Style.RESET_ALL}")
    log.info(f"{Fore.CYAN}Downloading: {course.name}{Style.RESET_ALL}")
    log.info(f"{Fore.CYAN}Mode: {'PDFs + HTML Pages' if save_html_pages else 'PDFs Only'}{Style.RESET_ALL}")
    log.info(f"{Fore.CYAN}{'=' * 70}{Style.RESET_ALL}\n")

    stats = dict(api_attachments=0, embedded_pdfs=0, html_pages=0, errors=0, skipped=0)

    def process_content(content, path, level=0, parent_title=None):
        indent       = "  " * level
        content_type = content.content_handler.id if content.content_handler else "unknown"

        log.info(f"{indent}{'📁' if content.has_children else '📄'} {content.title}")
        log.debug(f"{indent}   [type={content_type}] [id={content.id}]")

        child_path = os.path.join(path, content.title_safe) if content.has_children else path

        # 1. API attachments
        try:
            attachments = content.attachments()
            if attachments:
                log.info(f"{indent}   {Fore.GREEN}API Attachments: {len(attachments)}{Style.RESET_ALL}")
                for att in attachments:
                    ok = download_file(
                        content.client,
                        f"/learn/api/public/v1/courses/{content.course.id}"
                        f"/contents/{content.id}/attachments/{att.id}/download",
                        att.file_name, child_path)
                    if ok: stats['api_attachments'] += 1
                    else:  stats['skipped'] += 1
        except Exception as e:
            log.error(f"{indent}   {Fore.RED}✗ Attachment error: {e}{Style.RESET_ALL}")
            stats['errors'] += 1

        # 2. Embedded files (all non-image types)
        if content.body:
            file_links = extract_file_links_from_body(content.body)
            if file_links:
                log.info(f"{indent}   {Fore.CYAN}Embedded Files: {len(file_links)}{Style.RESET_ALL}")
                for p in file_links:
                    ok = download_file(content.client, p['url'], p['filename'], child_path,
                                       fallback_url=p.get('fallback_url', ''))
                    if ok: stats['embedded_pdfs'] += 1
                    else:  stats['skipped'] += 1

            # 3. HTML page
            if save_html_pages and content_type == "resource/x-bb-document":
                ok = save_html_page(content, child_path, content.client, parent_title=parent_title)
                if ok: stats['html_pages'] += 1
                else:  stats['skipped'] += 1

        # 4. Recurse
        if content.has_children:
            try:
                children = content.children()
                for child in children:
                    process_content(child, child_path, level + 1, parent_title=content.title)
            except Exception as e:
                log.error(f"{indent}   {Fore.RED}✗ Children error: {e}{Style.RESET_ALL}")
                stats['errors'] += 1

    base_path = os.path.join(save_location, course.name_safe)
    log.info(f"Saving to: {os.path.abspath(base_path)}")
    try:
        contents = course.contents()
    except Exception as e:
        log.error(f"{Fore.RED}✗ Could not get contents: {e}{Style.RESET_ALL}")
        return stats

    for content in contents:
        process_content(content, base_path)

    total = stats['api_attachments'] + stats['embedded_pdfs'] + stats['html_pages']
    log.info(f"\n{Fore.CYAN}{'=' * 70}{Style.RESET_ALL}")
    log.info(f"{Fore.CYAN}Done: {course.name}{Style.RESET_ALL}")
    log.info(f"  {Fore.GREEN}✓ API Attachments : {stats['api_attachments']}{Style.RESET_ALL}")
    log.info(f"  {Fore.GREEN}✓ Embedded Files  : {stats['embedded_pdfs']}{Style.RESET_ALL}")
    if save_html_pages:
        log.info(f"  {Fore.CYAN}✓ HTML Pages      : {stats['html_pages']}{Style.RESET_ALL}")
    log.info(f"  {Fore.CYAN}Total             : {total}{Style.RESET_ALL}")
    log.info(f"  {Fore.YELLOW}⚠ Skipped         : {stats['skipped']}{Style.RESET_ALL}")
    log.info(f"  {Fore.RED}✗ Errors          : {stats['errors']}{Style.RESET_ALL}")
    log.info(f"{Fore.CYAN}{'=' * 70}{Style.RESET_ALL}\n")
    return stats
# ─────────────────────────────────────────────────────────────────────────────


def main():
    log.info("=" * 70)
    log.info("ESPRIT Complete Course Downloader")
    log.info(f"Log: {log_filename}")
    log.info("=" * 70 + "\n")

    config = {}
    if os.path.exists('config.json'):
        try:
            with open('config.json') as f:
                config = json.load(f)
            log.info("✓ Loaded config.json")
        except Exception as e:
            log.warning(f"⚠ config.json error: {e}")

    username = config.get('username') or input("Username: ").strip()
    password = config.get('password') or getpass.getpass("Password: ")
    site     = config.get('site', 'https://esprit.blackboard.com')

    log.info(f"\n{Fore.CYAN}User:{Style.RESET_ALL} {username}")
    log.info(f"{Fore.CYAN}Site:{Style.RESET_ALL} {site}\n")
    log.info("Connecting...")

    try:
        client = BlackBoardClient(
            username=username, password=password, site=site,
            save_location='./downloads', thread_count=8,
            use_manifest=True, backup_files=False)

        success, response = client.login()
        if not success:
            log.error(f"{Fore.RED}✗ Login failed!{Style.RESET_ALL}")
            sys.exit(1)
        log.info(f"{Fore.GREEN}✓ Login successful!{Style.RESET_ALL}")

        courses = client.courses()
        log.info(f"\n{Fore.GREEN}✓ Found {len(courses)} courses:{Style.RESET_ALL}\n")
        for i, c in enumerate(courses, 1):
            log.info(f"  [{i:2d}] {c.name}")

        # ── mode: from config or prompt ──────────────────────────────────────
        cfg_mode = config.get('mode')
        if cfg_mode is not None:
            download_option = str(cfg_mode).strip()
            log.info(f"[config] mode = {download_option}")
        else:
            log.info(f"\n{Fore.YELLOW}{'=' * 70}")
            log.info("Download Options:")
            log.info("  [1] Files only (PDFs, docx, etc.)")
            log.info("  [2] Files + HTML pages (complete backup, images embedded)")
            log.info(f"{'=' * 70}{Style.RESET_ALL}")
            download_option = input("\nChoice (default: 1): ").strip() or "1"
        save_html_pages = (download_option == "2")

        # ── course: from config or prompt ─────────────────────────────────────
        cfg_course = config.get('course')
        if cfg_course is not None:
            choice = str(cfg_course).strip().lower()
            log.info(f"[config] course = {choice}")
        else:
            log.info(f"\n{Fore.YELLOW}{'=' * 70}")
            log.info("Course Selection:")
            log.info("  [a] Download ALL courses")
            log.info("  [#] Course number")
            log.info("  [q] Quit")
            log.info(f"{'=' * 70}{Style.RESET_ALL}")
            choice = input("\nChoice: ").strip().lower()

        all_stats = {}
        if choice == 'q':
            log.info("Goodbye!")
            sys.exit(0)
        elif choice == 'a':
            for c in courses:
                all_stats[c.name] = download_course_complete(c, save_html_pages=save_html_pages)
        else:
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(courses):
                    c = courses[idx]
                    all_stats[c.name] = download_course_complete(c, save_html_pages=save_html_pages)
                else:
                    log.error(f"{Fore.RED}✗ Invalid number{Style.RESET_ALL}")
            except ValueError:
                log.error(f"{Fore.RED}✗ Invalid input{Style.RESET_ALL}")

        if all_stats:
            ga = sum(s['api_attachments'] for s in all_stats.values())
            gp = sum(s['embedded_pdfs']   for s in all_stats.values())
            gh = sum(s['html_pages']       for s in all_stats.values())
            ge = sum(s['errors']           for s in all_stats.values())
            log.info(f"\n{'=' * 70}")
            log.info(f"GRAND TOTAL — {len(all_stats)} course(s)")
            log.info(f"  API Attachments : {ga}")
            log.info(f"  Embedded PDFs   : {gp}")
            log.info(f"  HTML Pages      : {gh}")
            log.info(f"  Total files     : {ga+gp+gh}")
            log.info(f"  Errors          : {ge}")
            log.info("=" * 70)

        log.info(f"\n{Fore.GREEN}✅ Done! Files -> ./downloads/{Style.RESET_ALL}")
        log.info(f"{Fore.GREEN}📋 Log  -> {log_filename}{Style.RESET_ALL}")

        # Always overwrite recent.log with the current run
        import shutil
        try:
            shutil.copy2(log_filename, './logs/recent.log')
        except Exception:
            pass

    except KeyboardInterrupt:
        log.warning(f"\n{Fore.YELLOW}⚠ Interrupted{Style.RESET_ALL}")
        import shutil; shutil.copy2(log_filename, './logs/recent.log')
        sys.exit(1)
    except Exception as e:
        log.error(f"\n{Fore.RED}✗ {e}{Style.RESET_ALL}")
        import traceback; traceback.print_exc()
        import shutil; shutil.copy2(log_filename, './logs/recent.log')
        sys.exit(1)


if __name__ == "__main__":
    main()
