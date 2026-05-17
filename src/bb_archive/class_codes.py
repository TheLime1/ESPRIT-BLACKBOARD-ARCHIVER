from __future__ import annotations

import re

CLASS_SUFFIX_RE = re.compile(r"__([0-9A-Za-z]+)\s*$")
CLASS_FAMILY_RE = re.compile(r"^(\d+[A-Z]+)")


def parse_course_class(course_name: str | None) -> str | None:
    if not course_name:
        return None
    match = CLASS_SUFFIX_RE.search(str(course_name).strip())
    if not match:
        return None
    return match.group(1).upper()


def clean_course_name(course_name: str | None) -> str:
    if not course_name:
        return "Untitled Course"
    return CLASS_SUFFIX_RE.sub("", str(course_name)).strip() or "Untitled Course"


def class_family(class_code: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z]", "", str(class_code or "")).upper()
    if not normalized:
        raise ValueError("class_code is required")

    match = CLASS_FAMILY_RE.match(normalized)
    if match:
        return match.group(1)
    return re.sub(r"\d+$", "", normalized) or normalized
