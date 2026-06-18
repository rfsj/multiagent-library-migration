from __future__ import annotations

from pathlib import Path

from catalog.search import (
    items_starting_with,
    normalize_catalog,
    search_by_keyword,
    uppercase_names,
)

ROWS = [
    "id,name,category",
    "1,Blue Widget, Electronics ",
    "2,red gadget, TOOLS",
    "3,Blue Doohickey,Electronics",
    "4,Green Gizmo,Garden",
    "5,bluetooth speaker,Electronics",
    "6, Red Wrench ,TOOLS",
]


def _csv(tmp_path: Path) -> Path:
    p = tmp_path / "catalog.csv"
    p.write_text("\n".join(ROWS) + "\n", encoding="utf-8")
    return p


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def _cols(frame):
    return list(frame.columns)


def test_search_by_keyword_case_insensitive(tmp_path):
    result = search_by_keyword(_csv(tmp_path), "blue")
    ids = {r["id"] for r in _records(result)}
    assert ids == {1, 3, 5}


def test_search_by_keyword_sorted_by_name(tmp_path):
    result = search_by_keyword(_csv(tmp_path), "blue")
    names = [r["name"].strip() for r in _records(result)]
    assert names == sorted(names)


def test_search_by_keyword_columns(tmp_path):
    assert _cols(search_by_keyword(_csv(tmp_path), "blue")) == ["id", "name", "category"]


def test_search_by_keyword_no_match_returns_empty(tmp_path):
    result = search_by_keyword(_csv(tmp_path), "zzznomatch")
    assert _records(result) == []


def test_normalize_catalog_strips_whitespace(tmp_path):
    result = normalize_catalog(_csv(tmp_path))
    records = _records(result)
    for r in records:
        assert r["name"] == r["name"].strip()
        assert r["category"] == r["category"].strip()


def test_normalize_catalog_lowercases_category(tmp_path):
    result = normalize_catalog(_csv(tmp_path))
    for r in _records(result):
        assert r["category"] == r["category"].lower()


def test_normalize_catalog_sorted(tmp_path):
    result = normalize_catalog(_csv(tmp_path))
    records = _records(result)
    pairs = [(r["category"], r["name"]) for r in records]
    assert pairs == sorted(pairs)


def test_normalize_catalog_columns(tmp_path):
    assert _cols(normalize_catalog(_csv(tmp_path))) == ["id", "name", "category"]


def test_items_starting_with_prefix(tmp_path):
    result = items_starting_with(_csv(tmp_path), "Blue")
    ids = {r["id"] for r in _records(result)}
    assert ids == {1, 3}


def test_items_starting_with_sorted_by_id(tmp_path):
    result = items_starting_with(_csv(tmp_path), "Blue")
    ids = [r["id"] for r in _records(result)]
    assert ids == sorted(ids)


def test_items_starting_with_columns(tmp_path):
    assert _cols(items_starting_with(_csv(tmp_path), "Blue")) == ["id", "name"]


def test_items_starting_with_no_match(tmp_path):
    result = items_starting_with(_csv(tmp_path), "ZZZ")
    assert _records(result) == []


def test_uppercase_names(tmp_path):
    result = uppercase_names(_csv(tmp_path))
    for r in _records(result):
        assert r["name"] == r["name"].upper()


def test_uppercase_names_sorted_by_id(tmp_path):
    result = uppercase_names(_csv(tmp_path))
    ids = [r["id"] for r in _records(result)]
    assert ids == sorted(ids)


def test_uppercase_names_columns(tmp_path):
    assert _cols(uppercase_names(_csv(tmp_path))) == ["id", "name"]
