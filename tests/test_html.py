from bb_archive.html import extract_embedded_files, extract_image_files, inject_page_navigation, process_html_body


def test_process_html_keeps_images_as_absolute_remote_links():
    body = '<p><img src="/bbcswebdav/x.png"></p><a href="/webapps/foo">open</a>'
    processed = process_html_body(body, "https://esprit.blackboard.com")

    assert 'src="https://esprit.blackboard.com/bbcswebdav/x.png"' in processed
    assert 'href="https://esprit.blackboard.com/webapps/foo"' in processed


def test_process_html_rewrites_downloaded_images_to_local_assets():
    body = '<p><img src="/bbcswebdav/x.png"></p>'
    processed = process_html_body(
        body,
        "https://esprit.blackboard.com",
        {"https://esprit.blackboard.com/bbcswebdav/x.png": "../images/_content_1/x.png"},
    )

    assert 'src="../images/_content_1/x.png"' in processed


def test_process_html_renders_downloaded_image_anchors_inline():
    body = """
    <a href="/images/_content_1/x.png" data-bbfile="{&quot;fileName&quot;:&quot;x.png&quot;,&quot;mimeType&quot;:&quot;image/png&quot;}">x</a>
    """
    processed = process_html_body(
        body,
        "https://esprit.blackboard.com",
        {"https://esprit.blackboard.com/images/_content_1/x.png": "../images/_content_1/x.png"},
    )

    assert '<img alt="x.png" decoding="async" loading="lazy" src="../images/_content_1/x.png"/>' in processed
    assert "<a" not in processed
    assert 'href="../images/_content_1/x.png"' not in processed
    assert "https://esprit.blackboard.com/images/_content_1/x.png" not in processed


def test_extract_image_files_finds_plain_and_bbfile_images():
    body = """
    <img src="/plain.png">
    <a href="/stable-image.png" data-bbfile="{&quot;fileName&quot;:&quot;inline.png&quot;,&quot;mimeType&quot;:&quot;image/png&quot;,&quot;resourceUrl&quot;:&quot;/session-image.png&quot;}">img</a>
    <a href="/doc.pdf" data-bbfile="{&quot;fileName&quot;:&quot;doc.pdf&quot;,&quot;mimeType&quot;:&quot;application/pdf&quot;}">doc</a>
    """

    images = extract_image_files(body, "https://esprit.blackboard.com")

    assert [image.filename for image in images] == ["inline.png", "plain.png"]
    assert images[0].url == "https://esprit.blackboard.com/stable-image.png"
    assert images[0].fallback_url == "https://esprit.blackboard.com/session-image.png"


def test_extract_embedded_files_skips_images_and_keeps_documents():
    body = """
    <a href="/doc.pdf" data-bbfile="{&quot;fileName&quot;:&quot;doc.pdf&quot;,&quot;mimeType&quot;:&quot;application/pdf&quot;}">doc</a>
    <a href="/img.png" data-bbfile="{&quot;fileName&quot;:&quot;img.png&quot;,&quot;mimeType&quot;:&quot;image/png&quot;}">img</a>
    """

    files = extract_embedded_files(body, "https://esprit.blackboard.com")

    assert len(files) == 1
    assert files[0].filename == "doc.pdf"
    assert files[0].url == "https://esprit.blackboard.com/doc.pdf"


def test_inject_page_navigation_adds_previous_and_next_links():
    page = "<!doctype html><html><body><main><h1>Current</h1></main></body></html>"

    processed = inject_page_navigation(
        page,
        current_label="Course / Current",
        progress_label="Page 2 of 3",
        previous_page={"href": "prev.html", "label": "Previous"},
        next_page={"href": "next.html", "label": "Next"},
    )

    assert processed.count("archive-page-nav") >= 2
    assert 'data-archive-page-nav="true"' in processed
    assert "text-decoration: none !important" in processed
    assert 'href="prev.html"' in processed
    assert 'href="next.html"' in processed
    assert "Page 2 of 3" in processed
