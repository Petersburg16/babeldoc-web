import pytest

from app.pages import count_pages, normalize_pages, parse_pages


def test_parse_matches_babeldoc_semantics():
    assert parse_pages("1,3-5,8-,-2") == [(1, 1), (3, 5), (8, -1), (1, 2)]


@pytest.mark.parametrize("spec", ["abc", "3-1", "0", "1,,2", "-"])
def test_parse_rejects_garbage(spec):
    with pytest.raises(ValueError):
        parse_pages(spec)


def test_count_dedupes_and_clamps():
    assert count_pages(None, 12) == 12
    assert count_pages("1-3,2-4", 12) == 4
    assert count_pages("10-", 12) == 3
    assert count_pages("20-30", 12) == 0


def test_normalize_handles_fullwidth_and_spaces():
    assert normalize_pages(" 1 – 3， 5 ") == "1-3,5"
    assert normalize_pages("   ") is None
