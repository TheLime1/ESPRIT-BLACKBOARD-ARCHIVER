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

class StripAnsi(logging.Formatter):
    _re = re.compile(r'\x1b\[[0-9;]*m')
    def format(self, record):
        return self._re.sub('', super().format(record))

_ch = logging.StreamHandler(sys.stdout)
_ch.setFormatter(logging.Formatter('%(message)s'))
log = logging.getLogger('esprit')
log.setLevel(logging.DEBUG)
log.addHandler(_ch)

# log_filename and file handler are added in main() after we know the BB class label
log_filename = None  # set in main()

def _setup_log_file(label: str) -> str:
    """
    Create the per-run log file using the Blackboard class label
    (e.g. '3AI') instead of the OS username.
    Returns the path so it can be copied to recent.log later.
    """
    global log_filename
    safe_label = re.sub(r'[^A-Za-z0-9_-]', '', label) or 'unknown'
    log_filename = f"./logs/download_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_label}.log"
    fh = logging.FileHandler(log_filename, encoding='utf-8')
    fh.setFormatter(StripAnsi('%(asctime)s  %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
    log.addHandler(fh)
    return log_filename

def _get_bb_class_label(client, courses=None) -> str:
    """
    Determine the student's class label (e.g. '3IA2') for use in the log filename.

    Priority order:
      1. Extract from enrolled course names — courses are named like 'Subject__3IA2',
         so we pull the suffix after '__' which is the most reliable source.
      2. studentId / externalId field on the user profile.
      3. batch_uid (strip trailing 4-digit year, e.g. 'ESE_ST_231JFT' → keep as-is
         if no 4-digit suffix found).
      4. OS username fallback.
    """
    # Strategy 1: parse class from course name suffix (e.g. "Financial Analysis__3IA2")
    if courses:
        for course in courses:
            name = course.name or ''
            if '__' in name:
                suffix = name.rsplit('__', 1)[-1].strip()
                # Accept short class codes: e.g. '3IA2', '2ING1', '1INFO'
                if suffix and re.match(r'^\d[A-Za-z]{1,6}\d*$', suffix):
                    return suffix
    # Strategy 2: user profile studentId / externalId
    try:
        resp = client.send_get_request(
            f"/learn/api/public/v1/users/{client.user_id}", silent_on_error=True)
        if resp and resp.status_code == 200:
            data = resp.json()
            student_id = data.get('studentId') or data.get('externalId') or ''
            if student_id:
                # Strip trailing 4-digit year suffix (e.g. '3AI2425' → '3AI')
                label = re.sub(r'\d{4}$', '', str(student_id)).strip()
                if label:
                    return label
    except Exception:
        pass
    # Strategy 3: batch_uid
    if client.batch_uid:
        label = re.sub(r'\d{4}$', '', str(client.batch_uid)).strip()
        if label:
            return label
    return client.username or 'unknown'
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

def parse_ultra_attempt_url(url: str) -> dict:
    """
    Parse a BB Ultra attempt review URL and return a dict of extracted IDs.
    Example URL:
      /ultra/courses/_20696_1/outline/assessment/_3025875_1/overview/attempt/_7728_1/review/...
      ?attemptId=_7728_1&columnId=_393277_1&contentId=_3025875_1&courseId=_20696_1
    Returns keys: courseId, contentId, columnId, attemptId  (any may be absent)
    """
    result = {}
    # Query-string params (most reliable)
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in ('courseId', 'contentId', 'columnId', 'attemptId'):
        vals = qs.get(key)
        if vals:
            result[key] = vals[0]
    # Path segments as fallback: /courses/_X/outline/assessment/_Y/overview/attempt/_Z
    path_re = re.compile(
        r'/courses/(?P<courseId>[^/]+)/outline/assessment/(?P<contentId>[^/]+)'
        r'(?:/overview/attempt/(?P<attemptId>[^/?]+))?')
    m = path_re.search(parsed.path)
    if m:
        for key in ('courseId', 'contentId', 'attemptId'):
            if m.group(key) and key not in result:
                result[key] = m.group(key)
    return result


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


# ── Assignment downloader ────────────────────────────────────────────────────
def download_assignment(content, save_path, client, save_html_pages=True, indent=''):
    """
    Download everything accessible for an assignment content item.

    Instructions source (proven by diagnosis):
      contentHandler.instructions  ← HTML string in the public content detail API
      (falls back to content.body / assessment endpoint if absent)

    Submission paths (two distinct flows):
      PATH A — Individual (no groupAttemptId):
        /learn/api/public/v1/courses/{cid}/gradebook/columns/{col}/attempts
        attempt.studentSubmission = HTML string
        No file list available on this path.

      PATH B — Group (groupAttemptId present):
        /learn/api/v1/courses/{cid}/gradebook/columns/{col}/groupAttempts
        (private non-public API, confirmed 200 for student role on group assignments)
        attempt.studentSubmission.rawText = HTML
        attempt.studentSubmissionFiles[].file.permanentUrl = /bbcswebdav/xid-... (downloads OK)
        Works for ALL file types (.ipynb, .docx, .pdf, etc.)

    Output folder layout:
      {assign_path}/
        instructions.html          ← contentHandler.instructions (raw HTML preserved)
        attachments/               ← instructor-attached files
        my_submissions/
          submission_{id}.html     ← student submission text (HTML)
          {filename}               ← student uploaded files (from permanentUrl)
    """
    course_id  = content.course.id
    content_id = content.id
    counts     = dict(instructions=0, attachments=0, submissions=0)

    title_safe  = re.sub(r'[<>:"/\\|?*]', '-', content.title or 'assignment').strip()
    assign_path = os.path.join(save_path, title_safe)
    os.makedirs(assign_path, exist_ok=True)

    log.info(f"{indent}   {Fore.CYAN}📝 Assignment: {content.title}{Style.RESET_ALL}")

    # ── Step 1: Fetch the full content detail to get contentHandler.instructions ──
    # Diagnosis proved: for x-bb-asmt-test-link the instructions live in
    # contentHandler.instructions (an HTML string), NOT in content.body or
    # the /assessments/{id} endpoint.
    content_detail = {}
    try:
        detail_resp = client.send_get_request(
            f"/learn/api/public/v1/courses/{course_id}/contents/{content_id}",
            silent_on_error=True)
        if detail_resp and detail_resp.status_code == 200:
            content_detail = detail_resp.json()
    except Exception as e:
        log.debug(f"{indent}      [detail] error: {e}")

    handler      = content_detail.get('contentHandler') or {}
    instructions = handler.get('instructions') or ''   # HTML string — PRIMARY source
    col_id_hint  = handler.get('gradeColumnId') or ''  # shortcut: col id already here

    # Fallback chain for instructions: content.body → assessment endpoint → Ultra outline
    if not instructions:
        instructions = content.body or ''
        if instructions:
            log.debug(f"{indent}      [instructions] from content.body")
    if not instructions:
        try:
            assess_resp = client.send_get_request(
                f"/learn/api/public/v1/courses/{course_id}/assessments/{content_id}",
                silent_on_error=True)
            if assess_resp and assess_resp.status_code == 200:
                ad = assess_resp.json()
                instructions = (ad.get('instructions') or ad.get('description') or
                                ad.get('body') or '')
                if instructions:
                    log.debug(f"{indent}      [instructions] from assessment endpoint")
        except Exception:
            pass
    if not instructions:
        # Ultra outline endpoint — stores instructions under 'description' or 'instructorNotes'
        try:
            outline_resp = client.send_get_request(
                f"/learn/api/public/v1/courses/{course_id}/contents/{content_id}/children",
                silent_on_error=True)
            if outline_resp and outline_resp.status_code == 200:
                for child in (outline_resp.json().get('results') or []):
                    candidate = (child.get('body') or child.get('description') or
                                 child.get('instructions') or '')
                    if candidate:
                        instructions = candidate
                        log.debug(f"{indent}      [instructions] from content children")
                        break
        except Exception:
            pass
    if not instructions:
        log.warning(f"{indent}      {Fore.YELLOW}⚠ No instructions found for: "
                    f"{content.title}{Style.RESET_ALL}")

    # ── Step 2: Save instructions.html (raw HTML, no stripping) ─────────────
    if instructions:
        dest = os.path.join(assign_path, 'instructions.html')
        if not os.path.isfile(dest):
            # Process body to embed images and absolutise links
            processed = process_body(instructions, client.session, client.site,
                                     indent=indent + '      ')
            page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{html_lib.escape(content.title or '')}</title>
<style>
body{{font-family:Arial,sans-serif;font-size:14px;line-height:1.6;max-width:820px;
     padding:24px 40px;color:#222;background:#fff;}}
h1{{font-size:1.4rem;font-weight:600;margin:0 0 1.2em 0;color:#1a1a1a;
    padding-bottom:.5em;border-bottom:2px solid #e0e0e0;}}
img{{max-width:100%;height:auto;display:block;margin:12px 0;}}
a{{color:#1a6fb5;word-break:break-all;}}
ul,ol{{padding-left:1.5em;margin:.5em 0;}} li{{margin-bottom:.2em;}}
table{{border-collapse:collapse;width:100%;margin:1em 0;}}
th,td{{border:1px solid #ccc;padding:6px 10px;text-align:left;}}
th{{background:#f4f4f4;font-weight:600;}}
p{{margin:.4em 0;}}
</style></head>
<body>
<h1>{html_lib.escape(content.title or '')}</h1>
{processed}
</body></html>"""
            try:
                with open(dest, 'w', encoding='utf-8') as f:
                    f.write(page)
                log.info(f"{indent}      {Fore.GREEN}✓ Saved: instructions.html{Style.RESET_ALL}")
                counts['instructions'] += 1
            except Exception as e:
                log.error(f"{indent}      {Fore.RED}✗ instructions.html: {e}{Style.RESET_ALL}")
        else:
            log.info(f"{indent}      {Fore.YELLOW}⚠ Exists: instructions.html{Style.RESET_ALL}")

    # ── Step 3: Instructor-attached files ────────────────────────────────────
    att_save_path = os.path.join(assign_path, 'attachments')

    # 1. Standard content /attachments endpoint
    try:
        for att in (content.attachments() or []):
            url = (f"/learn/api/public/v1/courses/{course_id}"
                   f"/contents/{content_id}/attachments/{att.id}/download")
            ok  = download_file(client, url, att.file_name, att_save_path)
            if ok:
                counts['attachments'] += 1
    except Exception as e:
        log.debug(f"{indent}      Instruction attachments error: {e}")

    # 2. Ultra assessment /fileAttachments endpoint (instructor-uploaded files)
    try:
        fa_resp = client.send_get_request(
            f"/learn/api/public/v1/courses/{course_id}"
            f"/assessments/{content_id}/questions",
            silent_on_error=True)
        # If the questions endpoint returns file attachment refs, pull them
        if fa_resp and fa_resp.status_code == 200:
            for q in (fa_resp.json().get('results') or []):
                for fa in (q.get('fileAttachments') or []):
                    fa_url  = fa.get('downloadUrl') or fa.get('url') or ''
                    fa_name = fa.get('fileName') or fa.get('name') or 'attachment'
                    if fa_url:
                        ok = download_file(client, fa_url, fa_name, att_save_path)
                        if ok:
                            counts['attachments'] += 1
    except Exception:
        pass

    # 3. Ultra /learn/api/public/v1/courses/{cid}/gradebook/columns endpoint
    #    Sometimes exposes the assignment instruction files.
    try:
        col_resp = client.send_get_request(
            f"/learn/api/public/v1/courses/{course_id}/gradebook/columns",
            silent_on_error=True)
        if col_resp and col_resp.status_code == 200:
            for col in (col_resp.json().get('results') or []):
                if col.get('contentId') == content_id:
                    col_id = col.get('id', '')
                    # Fetch attachments on the gradebook column
                    col_att_resp = client.send_get_request(
                        f"/learn/api/public/v1/courses/{course_id}"
                        f"/gradebook/columns/{col_id}/attempts",
                        silent_on_error=True)
                    break
    except Exception:
        pass

    # 4. Instruction attachments embedded in body HTML
    if instructions:
        for p in extract_file_links_from_body(instructions):
            ok = download_file(client, p['url'], p['filename'],
                               att_save_path,
                               fallback_url=p.get('fallback_url', ''))
            if ok:
                counts['attachments'] += 1

    # ── Step 4: Resolve gradebook columnId ───────────────────────────────────
    # col_id_hint may already be set from contentHandler.gradeColumnId (free, no RTT).
    # If not, discover via the gradebook columns listing.
    col_id = col_id_hint or ''
    if not col_id:
        try:
            col_resp = client.send_get_request(
                f"/learn/api/public/v1/courses/{course_id}/gradebook/columns",
                silent_on_error=True)
            if col_resp and col_resp.status_code == 200:
                for col in (col_resp.json().get('results') or []):
                    if col.get('contentId') == content_id:
                        col_id = col.get('id', '')
                        log.debug(f"{indent}      [col] discovered columnId={col_id}")
                        break
        except Exception as e:
            log.debug(f"{indent}      [col] discovery error: {e}")

    if not col_id:
        log.debug(f"{indent}      [col] no columnId found — no submissions to fetch")
        total = counts['instructions'] + counts['attachments'] + counts['submissions']
        if total:
            log.info(f"{indent}      → {counts['attachments']} attachment(s), "
                     f"{counts['submissions']} submission file(s)")
        return counts

    # ── Step 5: Fetch attempts — dynamic PATH A / PATH B ─────────────────────
    #
    # PATH B first: private /learn/api/v1/ groupAttempts endpoint.
    #   Returns the full attempt object with:
    #     studentSubmission.rawText  (HTML)
    #     studentSubmissionFiles[].file.permanentUrl  (all file types)
    #   lookup = { groupAssociationId: [attempt, ...] }
    #
    # PATH A fallback: public /gradebook/columns/{col}/attempts.
    #   Returns skeletal attempts with studentSubmission as a raw HTML string.
    #   No file list on this path.

    sub_path = os.path.join(assign_path, 'my_submissions')

    # ── PATH B: private groupAttempts (group assignments) ───────────────────
    path_b_attempts = []
    try:
        priv_r = client.session.get(
            f"{client.site}/learn/api/v1/courses/{course_id}"
            f"/gradebook/columns/{col_id}/groupAttempts",
            allow_redirects=True, timeout=15)
        if priv_r.status_code == 200:
            lookup = priv_r.json().get('lookup', {})
            for group_assoc_id, gas in lookup.items():
                for ga in gas:
                    ga['_group_assoc_id'] = group_assoc_id
                    path_b_attempts.append(ga)
            log.debug(f"{indent}      [PATH B] {len(path_b_attempts)} attempt(s) via private groupAttempts")
        else:
            log.debug(f"{indent}      [PATH B] HTTP {priv_r.status_code} — not a group assignment or no access")
    except Exception as e:
        log.debug(f"{indent}      [PATH B] error: {e}")

    # ── PATH A: public column attempts (individual assignments) ──────────────
    path_a_attempts = []
    if not path_b_attempts:
        try:
            pub_r = client.session.get(
                f"{client.site}/learn/api/public/v1/courses/{course_id}"
                f"/gradebook/columns/{col_id}/attempts",
                params={'limit': 50}, allow_redirects=True, timeout=15)
            if pub_r.status_code == 200:
                path_a_attempts = pub_r.json().get('results', [])
                log.debug(f"{indent}      [PATH A] {len(path_a_attempts)} attempt(s) via public column")
            else:
                log.debug(f"{indent}      [PATH A] HTTP {pub_r.status_code}")
        except Exception as e:
            log.debug(f"{indent}      [PATH A] error: {e}")

    all_attempts = path_b_attempts or path_a_attempts

    if not all_attempts:
        log.info(f"{indent}      {Fore.YELLOW}No submissions found{Style.RESET_ALL}")
        total = counts['instructions'] + counts['attachments'] + counts['submissions']
        if total:
            log.info(f"{indent}      → {counts['attachments']} attachment(s), "
                     f"{counts['submissions']} submission file(s)")
        return counts

    # ── Pick best attempt: latest NEEDS_GRADING else latest any ──────────────
    def _pick_best(attempts):
        graded = [a for a in attempts if a.get('status') in ('NEEDS_GRADING', 'NeedsGrading')]
        pool   = sorted(graded or attempts,
                        key=lambda a: a.get('attemptDate') or a.get('created') or '',
                        reverse=True)
        return pool[0] if pool else None

    best = _pick_best(all_attempts)
    if not best:
        total = counts['instructions'] + counts['attachments'] + counts['submissions']
        return counts

    attempt_id = best.get('id', 'unknown')
    status     = best.get('status', 'unknown')
    group_name = best.get('groupName', '')
    label      = f"[{status}]" + (f" [{group_name}]" if group_name else "")

    os.makedirs(sub_path, exist_ok=True)

    # ── Save attempt metadata as JSON ────────────────────────────────────────
    meta_dest = os.path.join(sub_path, f'attempt_{attempt_id}.json')
    if not os.path.isfile(meta_dest):
        try:
            with open(meta_dest, 'w', encoding='utf-8') as f:
                json.dump(best, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # ── Save submission text as HTML ─────────────────────────────────────────
    # PATH B: studentSubmission is a dict with rawText/displayText
    # PATH A: studentSubmission is a bare HTML string
    raw_sub = best.get('studentSubmission')
    if isinstance(raw_sub, dict):
        sub_html = raw_sub.get('rawText') or raw_sub.get('displayText') or ''
    elif isinstance(raw_sub, str):
        sub_html = raw_sub
    else:
        sub_html = (best.get('submissionText') or best.get('text') or
                    best.get('body') or best.get('groupSubmission') or '')
    sub_html = sub_html.strip() if sub_html else ''

    if sub_html:
        txt_dest = os.path.join(sub_path, f'submission_{attempt_id}.html')
        if not os.path.isfile(txt_dest):
            # Detect if already HTML; linkify bare URLs otherwise
            is_html = bool(re.search(r'<[a-zA-Z]', sub_html))
            url_re  = re.compile(r'https?://\S+', re.I)
            if not is_html and url_re.search(sub_html):
                escaped = html_lib.escape(sub_html)
                body_content = url_re.sub(
                    lambda m: f'<a href="{html_lib.unescape(m.group(0))}">{m.group(0)}</a>',
                    escaped)
                body_content = f'<pre style="white-space:pre-wrap">{body_content}</pre>'
            elif not is_html:
                body_content = f'<pre style="white-space:pre-wrap">{html_lib.escape(sub_html)}</pre>'
            else:
                body_content = sub_html

            page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Submission – {html_lib.escape(content.title or '')}</title>
<style>
body{{font-family:Arial,sans-serif;font-size:14px;line-height:1.6;max-width:820px;
     padding:24px 40px;color:#222;background:#fff;}}
h2{{font-size:1.1rem;font-weight:600;color:#444;margin:0 0 1.2em 0;
    padding-bottom:.4em;border-bottom:1px solid #e0e0e0;}}
a{{color:#1a6fb5;word-break:break-all;}}
pre{{background:#f5f5f5;padding:12px;border-radius:4px;overflow-x:auto;}}
strong{{font-weight:600;}}
</style></head>
<body>
<h2>{html_lib.escape(content.title or '')} — Submission {label}</h2>
{body_content}
</body></html>"""
            try:
                with open(txt_dest, 'w', encoding='utf-8') as f:
                    f.write(page)
                log.info(f"{indent}      {Fore.GREEN}✓ Submission text: "
                         f"submission_{attempt_id}.html {label}{Style.RESET_ALL}")
                counts['submissions'] += 1
            except Exception as e:
                log.error(f"{indent}      {Fore.RED}✗ Could not save submission text: {e}{Style.RESET_ALL}")
        else:
            log.info(f"{indent}      {Fore.YELLOW}⚠ Exists: submission_{attempt_id}.html{Style.RESET_ALL}")

    # ── Download submitted files (PATH B only — permanentUrl) ────────────────
    # PATH A has no file list. PATH B gives studentSubmissionFiles[].file.permanentUrl
    # which downloads fine for ALL mime types (.ipynb, .docx, .pdf, …).
    inline_files = best.get('studentSubmissionFiles') or []
    for isf in inline_files:
        file_obj = isf.get('file') or {}
        fname    = (isf.get('name') or isf.get('linkName') or
                    file_obj.get('fileName') or 'file')
        perm_url = file_obj.get('permanentUrl') or ''
        if perm_url and fname:
            ok = download_file(client, perm_url, fname, sub_path)
            if ok:
                counts['submissions'] += 1
                log.info(f"{indent}      {Fore.GREEN}✓ Submission file: "
                         f"{fname} {label}{Style.RESET_ALL}")
            else:
                log.warning(f"{indent}      {Fore.YELLOW}⚠ Failed: {fname}{Style.RESET_ALL}")

    if not sub_html and not inline_files:
        log.info(f"{indent}      {Fore.YELLOW}⚠ Attempt {attempt_id} {label}: "
                 f"no text or files (IN_PROGRESS or link-only){Style.RESET_ALL}")

    total = counts['instructions'] + counts['attachments'] + counts['submissions']
    if total:
        log.info(f"{indent}      → {counts['attachments']} attachment(s), "
                 f"{counts['submissions']} submission file(s)")
    return counts
# ─────────────────────────────────────────────────────────────────────────────


# ── Course downloader ─────────────────────────────────────────────────────────
def download_course_complete(course, save_location='./downloads', save_html_pages=False):
    log.info(f"\n{Fore.CYAN}{'=' * 70}{Style.RESET_ALL}")
    log.info(f"{Fore.CYAN}Downloading: {course.name}{Style.RESET_ALL}")
    log.info(f"{Fore.CYAN}Mode: {'PDFs + HTML Pages' if save_html_pages else 'PDFs Only'}{Style.RESET_ALL}")
    log.info(f"{Fore.CYAN}{'=' * 70}{Style.RESET_ALL}\n")

    stats = dict(api_attachments=0, embedded_pdfs=0, html_pages=0, assign_attachments=0, assign_submissions=0, errors=0, skipped=0)

    def process_content(content, path, level=0, parent_title=None):
        indent       = "  " * level
        content_type = content.content_handler.id if content.content_handler else "unknown"

        log.info(f"{indent}{'📁' if content.has_children else '📄'} {content.title}")
        log.debug(f"{indent}   [type={content_type}] [id={content.id}]")

        child_path = os.path.join(path, content.title_safe) if content.has_children else path

        # ── Assignments: instructions + attachments + submissions ────────────
        # BB Ultra uses 'resource/x-bb-asmt-test-link' for student-submitted
        # assignments; Classic BB uses 'resource/x-bb-assignment'.
        # Turnitin assignments also surface here.
        _ASSIGNMENT_TYPES = {
            "resource/x-bb-assignment",
            "resource/x-bb-asmt-test-link",
            "resource/x-turnitin-assignment",
        }
        if content_type in _ASSIGNMENT_TYPES:
            try:
                counts = download_assignment(content, child_path, content.client,
                                             save_html_pages=save_html_pages, indent=indent)
                stats['html_pages']          += counts['instructions']
                stats['assign_attachments']  += counts['attachments']
                stats['assign_submissions']  += counts['submissions']
            except Exception as e:
                log.error(f"{indent}   {Fore.RED}✗ Assignment error: {e}{Style.RESET_ALL}")
                stats['errors'] += 1

        else:
            # 1. API attachments (non-assignment content)
            # x-bb-file: single file served via /attachments (confirmed HTTP 200 in diagnosis)
            # x-bb-document / x-bb-folder / etc.: may also have attachments
            is_file_item = (content_type == "resource/x-bb-file")
            if is_file_item:
                # For x-bb-file the filename is in contentHandler.file.fileName
                try:
                    detail = content.client.send_get_request(
                        f"/learn/api/public/v1/courses/{content.course.id}"
                        f"/contents/{content.id}",
                        silent_on_error=True)
                    if detail and detail.status_code == 200:
                        ch_file = (detail.json().get('contentHandler') or {}).get('file') or {}
                        fname_hint = ch_file.get('fileName', '')
                        if fname_hint:
                            log.info(f"{indent}   {Fore.CYAN}\U0001f4ce File: {fname_hint}{Style.RESET_ALL}")
                except Exception:
                    pass
            try:
                attachments = content.attachments()
                if attachments:
                    label = "File" if is_file_item else "API Attachments"
                    log.info(f"{indent}   {Fore.GREEN}{label}: {len(attachments)}{Style.RESET_ALL}")
                    for att in attachments:
                        ok = download_file(
                            content.client,
                            f"/learn/api/public/v1/courses/{content.course.id}"
                            f"/contents/{content.id}/attachments/{att.id}/download",
                            att.file_name, child_path)
                        if ok: stats['api_attachments'] += 1
                        else:  stats['skipped'] += 1
            except Exception as e:
                log.error(f"{indent}   {Fore.RED}\u2717 Attachment error: {e}{Style.RESET_ALL}")
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

    total = stats['api_attachments'] + stats['embedded_pdfs'] + stats['html_pages'] + stats['assign_attachments'] + stats['assign_submissions']
    log.info(f"\n{Fore.CYAN}{'=' * 70}{Style.RESET_ALL}")
    log.info(f"{Fore.CYAN}Done: {course.name}{Style.RESET_ALL}")
    log.info(f"  {Fore.GREEN}✓ API Attachments : {stats['api_attachments']}{Style.RESET_ALL}")
    log.info(f"  {Fore.GREEN}✓ Embedded Files  : {stats['embedded_pdfs']}{Style.RESET_ALL}")
    if stats['assign_attachments'] or stats['assign_submissions']:
        log.info(f"  {Fore.GREEN}✓ Assign Files    : {stats['assign_attachments']}{Style.RESET_ALL}")
        log.info(f"  {Fore.GREEN}✓ Submissions     : {stats['assign_submissions']}{Style.RESET_ALL}")
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
    log.info("=" * 70 + "\n")

    config = {}
    if os.path.exists('config.json'):
        try:
            with open('config.json') as f:
                config = json.load(f)
            log.info("✓ Loaded config.json")
        except Exception as e:
            log.warning(f"⚠ config.json error: {e}")

    custom_path = config.get('custom_path', None)
    if custom_path:
        save_location = os.path.abspath(custom_path)
        # Ensure the directory exists
        os.makedirs(save_location, exist_ok=True)
    else:
        save_location = './downloads'
        os.makedirs(save_location, exist_ok=True)

    username = config.get('username') or input("Username: ").strip()
    password = config.get('password') or getpass.getpass("Password: ")
    site     = config.get('site', 'https://esprit.blackboard.com')

    log.info(f"\n{Fore.CYAN}User:{Style.RESET_ALL} {username}")
    log.info(f"{Fore.CYAN}Site:{Style.RESET_ALL} {site}\n")
    log.info("Connecting...")

    try:
        client = BlackBoardClient(
            username=username, password=password, site=site,
            save_location=save_location, thread_count=8,
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

        # Now that we have courses, determine label and open the log file
        bb_label = _get_bb_class_label(client, courses)
        _setup_log_file(bb_label)

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
            ga  = sum(s['api_attachments']   for s in all_stats.values())
            gp  = sum(s['embedded_pdfs']     for s in all_stats.values())
            gh  = sum(s['html_pages']        for s in all_stats.values())
            gaa = sum(s['assign_attachments'] for s in all_stats.values())
            gs  = sum(s['assign_submissions'] for s in all_stats.values())
            ge  = sum(s['errors']            for s in all_stats.values())
            log.info(f"\n{'=' * 70}")
            log.info(f"GRAND TOTAL — {len(all_stats)} course(s)")
            log.info(f"  API Attachments  : {ga}")
            log.info(f"  Embedded Files   : {gp}")
            if gaa or gs:
                log.info(f"  Assign Files     : {gaa}")
                log.info(f"  Submissions      : {gs}")
            log.info(f"  HTML Pages       : {gh}")
            log.info(f"  Total files      : {ga+gp+gh+gaa+gs}")
            log.info(f"  Errors           : {ge}")
            log.info("=" * 70)

        log.info(f"\n{Fore.GREEN}✅ Done! Files -> ./downloads/{Style.RESET_ALL}")
        log.info(f"{Fore.GREEN}📋 Log  -> {log_filename}{Style.RESET_ALL}")

        # Always overwrite recent.log with the current run
        import shutil
        try:
            if log_filename:
                shutil.copy2(log_filename, './logs/recent.log')
        except Exception:
            pass

    except KeyboardInterrupt:
        log.warning(f"\n{Fore.YELLOW}⚠ Interrupted{Style.RESET_ALL}")
        import shutil
        if log_filename:
            shutil.copy2(log_filename, './logs/recent.log')
        sys.exit(1)
    except Exception as e:
        log.error(f"\n{Fore.RED}✗ {e}{Style.RESET_ALL}")
        import traceback; traceback.print_exc()
        import shutil
        if log_filename:
            shutil.copy2(log_filename, './logs/recent.log')
        sys.exit(1)


if __name__ == "__main__":
    main()
