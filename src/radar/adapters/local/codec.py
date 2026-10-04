"""Local durable DTO encoding, deliberately separate from canonical wire JSON."""
import dataclasses
from datetime import datetime
from decimal import Decimal
from enum import Enum
import json

from radar.domain import core
from radar.domain.verticals import products
from radar.ports import types, workflow

CLASSES = {c.__name__: c for module in (core, products, types, workflow)
           for c in vars(module).values() if isinstance(c, type) and dataclasses.is_dataclass(c)}
ENUMS = {c.__name__: c for c in (core.Knowledge, types.LedgerState)}


def encode(value):
    if isinstance(value, Enum):
        return {'@enum': type(value).__name__, 'value': value.value}
    if dataclasses.is_dataclass(value):
        return {'@type': type(value).__name__, 'fields': {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}}
    if isinstance(value, datetime):
        return {'@datetime': value.isoformat()}
    if isinstance(value, Decimal):
        return {'@decimal': str(value)}
    if isinstance(value, tuple):
        return {'@tuple': [encode(v) for v in value]}
    if isinstance(value, dict):
        return {'@mapping': [[k,encode(v)] for k,v in value.items()]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    return value


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if '@mapping' in value:
        return {k:decode(v) for k,v in value['@mapping']}
    if '@enum' in value:
        return ENUMS[value['@enum']](value['value'])
    if '@type' in value:
        return CLASSES[value['@type']](**{k: decode(v) for k, v in value['fields'].items()})
    if '@datetime' in value:
        return datetime.fromisoformat(value['@datetime'])
    if '@decimal' in value:
        return Decimal(value['@decimal'])
    if '@tuple' in value:
        return tuple(decode(v) for v in value['@tuple'])
    return {k: decode(v) for k, v in value.items()}


def dumps(value):
    return json.dumps(encode(value), sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def loads(value):
    return decode(json.loads(value))
