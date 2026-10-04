"""Validate instances against the shared JSON Schema contracts in contracts/."""

from datetime import date, datetime
from functools import cache
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

# ponytail: repo-relative path; package the schemas when radar ships as a wheel.
CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
MAX_BYTES = 32768
FORMATS = FormatChecker(formats=())
FORMATS.checks("date-time", raises=ValueError)(lambda value: not isinstance(value, str) or datetime.fromisoformat(value))
FORMATS.checks("date", raises=ValueError)(lambda value: not isinstance(value, str) or date.fromisoformat(value))


def load_schema(name):
    """Return the schema dict for a name like 'envelope.v1'."""
    return json.loads((CONTRACTS / f"{name}.json").read_text(encoding="utf-8"))


@cache
def registry():
    """All contracts/*.vN.json keyed by $id; nothing is fetched over the network."""
    schemas = (json.loads(path.read_text(encoding="utf-8")) for path in CONTRACTS.glob("*.v[0-9]*.json"))
    return Registry().with_resources((schema["$id"], Resource.from_contents(schema)) for schema in schemas)


@cache
def _validator(name):
    return Draft202012Validator(load_schema(name), registry=registry(), format_checker=FORMATS)


def serialized_size(instance):
    return len(json.dumps(instance, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def validate(schema_name, instance):
    """Raise jsonschema.ValidationError if instance breaks the schema or the 32 KiB limit."""
    size = serialized_size(instance)
    if size > MAX_BYTES:
        raise ValidationError(f"serialized size {size} exceeds {MAX_BYTES} bytes")
    _validator(schema_name).validate(instance)
