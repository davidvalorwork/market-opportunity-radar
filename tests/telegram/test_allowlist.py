"""Phone allowlist: strict document validation, normalization, no disclosure. Synthetic numbers only."""

import pytest

from radar.adapters.telegram.allowlist import PhoneAllowlist, normalize_phone
from telegram_fakes import ALLOWLIST_JSON, COLLAB_PHONE, OWNER_PHONE


def doc(*entries, **extra):
    return {"schema_version": 1, "entries": list(entries), **extra}


def entry(phone=OWNER_PHONE, role="owner", **extra):
    return {"phone_e164": phone, "role": role, **extra}


def test_lookup_returns_role_or_none():
    allowlist = PhoneAllowlist.from_json(ALLOWLIST_JSON)
    assert allowlist.lookup(OWNER_PHONE) == "owner"
    assert allowlist.lookup(COLLAB_PHONE) == "collaborator"
    assert allowlist.lookup("+10000000009") is None
    assert allowlist.lookup(None) is None
    assert PhoneAllowlist(doc()).lookup(OWNER_PHONE) is None  # empty list is valid: nobody enrolls


def test_from_file(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text(ALLOWLIST_JSON, encoding="utf-8")
    assert PhoneAllowlist.from_file(path).lookup(COLLAB_PHONE) == "collaborator"


@pytest.mark.parametrize("phone", ["10000000000", "+0000000000", "+123456", "+1234567890123456", "+1 000 000 0000",
                                   "+1000000000a", "", None, 10000000000, "+１0000000000"])
def test_bad_e164_rejected_without_echoing_it(phone):
    with pytest.raises(ValueError) as error:
        PhoneAllowlist(doc(entry(phone=phone)))
    assert str(error.value) == "allowlist entry 0: invalid phone_e164"  # index only, never the value


@pytest.mark.parametrize("role", ["admin", "Owner", "", None, 1])
def test_unknown_role_rejected(role):
    with pytest.raises(ValueError, match="invalid role"):
        PhoneAllowlist(doc(entry(role=role)))


def test_duplicate_rejected_without_echoing_it():
    with pytest.raises(ValueError, match="entry 1: duplicate phone_e164") as error:
        PhoneAllowlist(doc(entry(), entry(role="collaborator")))
    assert OWNER_PHONE not in str(error.value)


@pytest.mark.parametrize("document", [
    doc(extra=1), doc(entry(name="x")), {"entries": []}, {"schema_version": 1},
    {"schema_version": 2, "entries": []}, {"schema_version": True, "entries": []}, {"schema_version": "1", "entries": []},
    doc({"role": "owner"}),
    {"schema_version": 1, "entries": {}}, [], "x", None, doc("not-an-object"),
])
def test_bad_structure_and_extra_keys_rejected(document):
    with pytest.raises(ValueError):
        PhoneAllowlist(document)


def test_invalid_json_rejected_without_echoing_it():
    with pytest.raises(ValueError, match="not valid JSON") as error:
        PhoneAllowlist.from_json('{"schema_version": 1, "entries": [{"phone_e164": "+10000000000"')
    assert error.value.__cause__ is None and "+10000000000" not in str(error.value)


def test_repr_and_str_hide_entries():
    allowlist = PhoneAllowlist.from_json(ALLOWLIST_JSON)
    for text in (repr(allowlist), str(allowlist), f"{allowlist}", repr([allowlist])):
        assert "0000000" not in text and "owner" not in text and "collaborator" not in text


@pytest.mark.parametrize("raw, expected", [
    ("+10000000001", "+10000000001"),
    ("10000000001", "+10000000001"),         # Telegram often omits the +
    ("1 000 000 0001", "+10000000001"),      # spaced
    (" +1 000 000 0001 ", "+10000000001"),
    ("+1 000\t0000001", "+10000000001"),
    ("", None), ("+", None), ("abc", None), ("++10000000001", None), ("+1-000-000-0001", None),
    ("0000000001", None), ("123", None), (None, None), (10000000001, None),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected
