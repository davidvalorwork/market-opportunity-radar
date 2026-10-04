"""Immutable internal proposal snapshots, distinct from transport envelopes."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import Protocol

from radar.domain.core import utc
from radar.ports.types import BlobPointer, InlineButton


class TaskError(ValueError):
    """Static technical reason, never raw user/model/provider content."""


def canonical(document):
    return json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def content_hash(document):
    return sha256(canonical(document).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class Budget:
    calls: int = 8
    messages: int = 0
    max_usd: Decimal = Decimal('0.25')

    def __post_init__(self):
        if type(self.calls) is not int or type(self.messages) is not int or min(self.calls, self.messages) < 0 or not isinstance(self.max_usd, Decimal) or not self.max_usd.is_finite() or self.max_usd < 0:
            raise TaskError('invalid_budget')

    def fits(self, ceiling):
        return self.calls <= ceiling.calls and self.messages <= ceiling.messages and self.max_usd <= ceiling.max_usd

    def document(self):
        return {'calls': self.calls, 'message_limit': self.messages, 'max_usd': str(self.max_usd)}


@dataclass(frozen=True)
class Authority:
    """Host-issued scope, not constructed from model fields; adapter rechecks it."""
    owner_ref: str
    actor_ref: str
    capabilities: tuple[tuple[str, str], ...]
    expires_at: datetime
    ceiling: Budget = Budget(20, 5, Decimal('1.00'))
    sessions: tuple[tuple[str, int], ...] = ()

    def __post_init__(self):
        utc(self.expires_at)
        opaque = r'[a-z][a-z0-9_]{0,31}:[A-Za-z0-9_-]*[A-Za-z][A-Za-z0-9_-]*'
        if any(not isinstance(ref, str) or len(ref) > 129 or not re.fullmatch(opaque, ref) for ref in (self.owner_ref, self.actor_ref)) or not isinstance(self.ceiling, Budget):
            raise TaskError('invalid_authority')
        if not isinstance(self.capabilities, tuple) or any(not isinstance(pair, tuple) or len(pair) != 2 or not all(isinstance(value, str) for value in pair) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', pair[0]) or len(pair[1]) > 129 or not re.fullmatch(opaque, pair[1]) for pair in self.capabilities) or len(dict(self.capabilities)) != len(self.capabilities):
            raise TaskError('invalid_authority')
        if not isinstance(self.sessions, tuple) or any(not isinstance(pair, tuple) or len(pair) != 2 or not isinstance(pair[0], str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,31}:[a-z0-9][a-z0-9-]{0,62}', pair[0]) or type(pair[1]) is not int or pair[1] < 1 for pair in self.sessions) or len(dict(self.sessions)) != len(self.sessions):
            raise TaskError('invalid_authority')


@dataclass(frozen=True)
class Plan:
    snapshot: str = field(repr=False)
    content_hash: str
    budget: Budget
    capability_refs: tuple[str, ...]
    missing_fields: tuple[str, ...]
    approval_steps: tuple[str, ...]

    @property
    def ready(self):
        return not self.missing_fields


@dataclass(frozen=True)
class Proposal:
    task_ref: str
    owner_ref: str
    actor_ref: str
    version: int
    status: str
    snapshot: str = field(repr=False)
    content_hash: str
    expires_at: datetime
    confirmation_hash: str | None  # Intent only; never an outbound ledger Approval.
    buttons: tuple[InlineButton, ...] = field(repr=False)
    replayed: bool = False

    @property
    def document(self):
        return json.loads(self.snapshot)


class ProposalRepository(Protocol):
    def create(self, *, authority: Authority, event_ref: str, plan: Plan, expires_at: datetime, now: datetime) -> Proposal: ...
    def get(self, *, authority: Authority, task_ref: str, now: datetime) -> Proposal: ...
    def act(self, *, authority: Authority, callback_ref: str, event_ref: str, now: datetime) -> Proposal: ...
    def correct(self, *, authority: Authority, task_ref: str, expected_version: int, plan: Plan, now: datetime) -> Proposal: ...
    def claim_parse(self, *, authority: Authority, event_ref: str, input_hash: str, context_refs: tuple, max_cost: Decimal, now: datetime) -> dict | None: ...
    def finish_parse(self, *, authority: Authority, event_ref: str, input_hash: str, document: dict, now: datetime) -> None: ...


class TaskDocumentVault(Protocol):
    """Trusted boundary: immutable authenticated encryption, owner-bound references.

    Production must supply a real encrypted backend. No plaintext fallback is allowed.
    The ciphertext hash is transport integrity, not authentication of its producer.
    """
    def seal(self, *, owner_ref: str, plaintext: bytes) -> BlobPointer: ...
    def open(self, *, owner_ref: str, pointer: BlobPointer) -> bytes: ...
