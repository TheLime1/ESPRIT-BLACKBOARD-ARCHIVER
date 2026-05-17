import pytest

from bb_archive.class_codes import class_family, clean_course_name, parse_course_class


@pytest.mark.parametrize(
    ("class_code", "expected"),
    [
        ("1A1", "1A"),
        ("1A45", "1A"),
        ("1B5", "1B"),
        ("4SAE11", "4SAE"),
        ("4SAE5", "4SAE"),
        ("4BI1", "4BI"),
    ],
)
def test_class_family(class_code, expected):
    assert class_family(class_code) == expected


def test_course_name_suffix_parsing():
    assert parse_course_class("Gestion de projet__4SAE11") == "4SAE11"
    assert clean_course_name("Gestion de projet__4SAE11") == "Gestion de projet"
