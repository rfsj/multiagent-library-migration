from __future__ import annotations

from pathlib import Path

from crm.contacts import find_by_domain, normalize_contacts
from crm.interactions import interaction_summary, recent_interactions

CONTACT_ROWS = [
    "contact_id,first_name,last_name,email,phone",
    "CO1,Alice,Smith, alice@example.com ,+1-555-0001",
    "CO2,Bob,Jones,BOB@company.org ,555.0002",
    "CO3,Carol,Davis, carol@example.com ,555-0003",
]

INTERACTION_ROWS = [
    "interaction_id,contact_id,timestamp,type",
    "I1,CO1,2024-10-15 10:00:00,email",
    "I2,CO2,2024-10-20 14:30:00,call",
    "I3,CO1,2024-11-01 09:00:00,email",
    "I4,CO3,2024-11-15 16:00:00,meeting",
    "I5,CO2,2024-11-18 11:00:00,call",
]


def _csv(tmp_path: Path, rows: list[str], name: str) -> Path:
    p = tmp_path / name
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def _columns(frame):
    return list(frame.columns)


def _paths(tmp_path):
    c = _csv(tmp_path, CONTACT_ROWS, "contacts.csv")
    i = _csv(tmp_path, INTERACTION_ROWS, "interactions.csv")
    return c, i


def test_normalize_contacts_columns(tmp_path):
    c, _ = _paths(tmp_path)
    assert _columns(normalize_contacts(c)) == [
        "contact_id",
        "full_name",
        "email",
        "phone_clean",
    ]


def test_normalize_contacts_email_lowercase(tmp_path):
    c, _ = _paths(tmp_path)
    result = {r["contact_id"]: r for r in _records(normalize_contacts(c))}
    assert result["CO2"]["email"] == "bob@company.org"


def test_normalize_contacts_email_stripped(tmp_path):
    c, _ = _paths(tmp_path)
    result = {r["contact_id"]: r for r in _records(normalize_contacts(c))}
    assert result["CO1"]["email"] == "alice@example.com"
    assert result["CO3"]["email"] == "carol@example.com"


def test_normalize_contacts_full_name(tmp_path):
    c, _ = _paths(tmp_path)
    result = {r["contact_id"]: r["full_name"] for r in _records(normalize_contacts(c))}
    assert result["CO1"] == "Alice Smith"
    assert result["CO2"] == "Bob Jones"


def test_normalize_contacts_phone_digits_only(tmp_path):
    c, _ = _paths(tmp_path)
    result = {
        r["contact_id"]: r["phone_clean"] for r in _records(normalize_contacts(c))
    }
    assert result["CO1"] == "15550001"
    assert result["CO2"] == "5550002"
    assert result["CO3"] == "5550003"


def test_find_by_domain_columns(tmp_path):
    c, _ = _paths(tmp_path)
    assert _columns(find_by_domain(c, "example.com")) == ["contact_id", "email"]


def test_find_by_domain_filters_correctly(tmp_path):
    c, _ = _paths(tmp_path)
    result = {r["contact_id"] for r in _records(find_by_domain(c, "example.com"))}
    assert result == {"CO1", "CO3"}


def test_find_by_domain_excludes_other(tmp_path):
    c, _ = _paths(tmp_path)
    result = {r["contact_id"] for r in _records(find_by_domain(c, "company.org"))}
    assert result == {"CO2"}


def test_recent_interactions_columns(tmp_path):
    c, i = _paths(tmp_path)
    assert _columns(recent_interactions(c, i)) == [
        "interaction_id",
        "contact_name",
        "timestamp",
        "type",
    ]


def test_recent_interactions_excludes_old(tmp_path):
    c, i = _paths(tmp_path)
    ids = {r["interaction_id"] for r in _records(recent_interactions(c, i, days=30))}
    assert "I1" not in ids


def test_recent_interactions_includes_recent(tmp_path):
    c, i = _paths(tmp_path)
    ids = {r["interaction_id"] for r in _records(recent_interactions(c, i, days=30))}
    assert {"I2", "I3", "I4", "I5"}.issubset(ids)


def test_recent_interactions_contact_name(tmp_path):
    c, i = _paths(tmp_path)
    result = {
        r["interaction_id"]: r["contact_name"]
        for r in _records(recent_interactions(c, i))
    }
    assert result["I3"] == "Alice Smith"
    assert result["I5"] == "Bob Jones"


def test_interaction_summary_columns(tmp_path):
    _, i = _paths(tmp_path)
    assert _columns(interaction_summary(i)) == ["month", "type", "count"]


def test_interaction_summary_october(tmp_path):
    _, i = _paths(tmp_path)
    result = {
        (r["month"], r["type"]): r["count"] for r in _records(interaction_summary(i))
    }
    assert result[("2024-10", "email")] == 1
    assert result[("2024-10", "call")] == 1


def test_interaction_summary_november(tmp_path):
    _, i = _paths(tmp_path)
    result = {
        (r["month"], r["type"]): r["count"] for r in _records(interaction_summary(i))
    }
    assert result[("2024-11", "call")] == 1
    assert result[("2024-11", "email")] == 1
    assert result[("2024-11", "meeting")] == 1


def test_interaction_summary_sorted(tmp_path):
    _, i = _paths(tmp_path)
    result = _records(interaction_summary(i))
    pairs = [(r["month"], r["type"]) for r in result]
    assert pairs == sorted(pairs)
