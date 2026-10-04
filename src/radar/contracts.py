"""Validate instances against the shared JSON Schema contracts shipped in radar/schemas/."""

from datetime import date, datetime
from functools import cache
from importlib.resources import files
import json
import re

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

SCHEMAS = files("radar") / "schemas"
MAX_BYTES = 32768
FORMATS = FormatChecker(formats=())
FORMATS.checks("date-time", raises=ValueError)(lambda value: not isinstance(value, str) or datetime.fromisoformat(value))
FORMATS.checks("date", raises=ValueError)(lambda value: not isinstance(value, str) or date.fromisoformat(value))


def schema_names():
    """Names like 'envelope.v1' for every shipped schema."""
    return sorted(entry.name[:-5] for entry in SCHEMAS.iterdir() if re.search(r"\.v\d+\.json$", entry.name))


def load_schema(name):
    """Return the schema dict for a name like 'envelope.v1'."""
    return json.loads((SCHEMAS / f"{name}.json").read_text(encoding="utf-8"))


@cache
def registry():
    """All shipped schemas keyed by $id; nothing is fetched over the network."""
    schemas = (load_schema(name) for name in schema_names())
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
