import json

from bb_archive.storage import ArchiveWriter


def test_classes_index_updates_idempotently(tmp_path):
    writer = ArchiveWriter(tmp_path, class_code="4SAE11", mode="html")
    writer.update_classes_index(
        stats={"courses": 2, "html_pages": 5, "images": 3, "attachments": 0, "errors": 0},
        generated_at="now",
    )
    writer.update_classes_index(
        stats={"courses": 2, "html_pages": 5, "images": 3, "attachments": 0, "errors": 0},
        generated_at="later",
    )

    data = json.loads((tmp_path / "classes.json").read_text(encoding="utf-8"))

    assert data["classes"] == [
        {
            "family": "4SAE",
            "classCodes": ["4SAE11"],
            "modes": ["html"],
            "latestRunAt": "later",
            "url": "classes/4SAE/4SAE11/index.json",
            "latestClassCode": "4SAE11",
            "courseCount": 2,
            "htmlPages": 5,
            "images": 3,
            "attachments": 0,
            "errors": 0,
        }
    ]
