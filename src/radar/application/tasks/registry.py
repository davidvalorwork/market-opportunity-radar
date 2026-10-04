"""Host-owned composable primitives. Registration documents support, not execution."""
from dataclasses import dataclass
from types import MappingProxyType
import re
from .models import TaskError


@dataclass(frozen=True)
class Operation:
    name: str
    required_fields: tuple[str, ...] = ()
    requires_session: bool = False
    requires_effect_approval: bool = False
    default_calls: int = 1

    def __post_init__(self):
        if not isinstance(self.name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', self.name) or not isinstance(self.required_fields, tuple) or any(not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name) for name in self.required_fields) or len(set(self.required_fields)) != len(self.required_fields) or type(self.default_calls) is not int or self.default_calls < 1 or type(self.requires_session) is not bool or type(self.requires_effect_approval) is not bool:
            raise TaskError('invalid_operation')


class OperationRegistry:
    def __init__(self, operations=None, *, revision='general-primitives-v1'):
        operations = tuple(operations) if operations is not None else (
            Operation('search', ('query',)), Operation('read', ('source_refs',)),
            Operation('extract', ('field_names',)), Operation('inform'), Operation('compose'),
            Operation('contact', ('recipient_refs', 'private_ref', 'purpose'), True, True),
            Operation('follow', ('recipient_refs', 'private_ref', 'purpose'), True, True),
            Operation('schedule', ('schedule',)),
        )
        if len({op.name for op in operations}) != len(operations):
            raise TaskError('duplicate_operation')
        self._operations = MappingProxyType({op.name: op for op in operations})
        self.revision = revision

    def get(self, name):
        try:
            return self._operations[name]
        except KeyError:
            raise TaskError('unsupported_operation') from None

    @property
    def names(self):
        return tuple(self._operations)
