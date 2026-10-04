"""Internal control DTOs, not another definition of the JSON wire schemas.

EnvelopeDocument is a document validated by boundary adapters with
radar.contracts.validate('envelope.v1', ...). Never put decrypted payloads in it.
OwnerScope is an opaque authorization scope, not proof of authentication.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Mapping, TypeAlias

JSONValue: TypeAlias = None | bool | int | float | str | list['JSONValue'] | dict[str, 'JSONValue']
EnvelopeDocument: TypeAlias = Mapping[str, JSONValue]
OwnerScope: TypeAlias = str


class ConditionalConflict(Exception):
    """Stale version, wrong owner or mismatching replay; no partial commit."""


class LedgerState(str, Enum):
    PROPOSED = 'proposed'
    APPROVED = 'approved'
    DISPATCH_COMMITTED = 'dispatch_committed'
    PROVIDER_CONFIRMED = 'provider_confirmed'
    SEND_UNCERTAIN = 'send_uncertain'


@dataclass(frozen=True)
class BlobPointer:
    blob_key: str
    sha256: str
    recipient_scope: str | None = None


@dataclass(frozen=True)
class Receipt:
    """Stable channel event key plus digest of the authorized command."""

    channel: str
    event_ref: str
    command_hash: str


@dataclass(frozen=True)
class OutboxEntry:
    envelope: EnvelopeDocument = field(repr=False)
    destination: str
    group_ref: str | None = None
    version: int = 0


@dataclass(frozen=True)
class AcceptedCommand:
    operation_id: str
    message_id: str
    replayed: bool


@dataclass(frozen=True)
class Approval:
    """Authorization binds actor, destination, payload, session and expiry."""

    actor_ref: str
    recipient_ref: str
    session_ref: str
    session_version: int
    content_hash: str
    purpose: str
    expires_at: datetime


@dataclass(frozen=True)
class LedgerRecord:
    operation_id: str
    state: LedgerState
    version: int
    content_hash: str
    recipient_ref: str
    purpose: str
    session_ref: str
    session_version: int
    approval: Approval | None = None
    provider_message_ref: str | None = None


@dataclass(frozen=True)
class Lease:
    """Only a LeaseStore acquisition yields this token; never wire authority."""

    session_ref: str
    worker_ref: str
    token: str = field(repr=False)
    expires_at: datetime
    version: int


@dataclass(frozen=True)
class PendingOutbox:
    entries: tuple[OutboxEntry, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class QueueDelivery:
    envelope: EnvelopeDocument = field(repr=False)
    acknowledgement_ref: str = field(repr=False)


@dataclass(frozen=True)
class InlineButton:
    text: str
    callback_ref: str


@dataclass(frozen=True)
class UIMessage:
    message_ref: str
    conversation_ref: str


@dataclass(frozen=True)
class Capability:
    platform: str
    backend: str
    operation: str
    status: str
    source: str
    checked_on: date
    authorized: bool


@dataclass(frozen=True)
class DatedRate:
    """Provider observation; domain FxRate applies economic validation."""

    base_currency: str
    quote_currency: str
    value: Decimal
    observed_at: datetime
    source_ref: str


@dataclass(frozen=True)
class StructuredRequest:
    schema_name: str
    schema_version: int
    model_ref: str
    prompt_version: str
    content_hash: str
    language: str
    privacy_scope: str
    public_input: str = field(repr=False)
    max_tokens: int
    max_cost: Decimal


@dataclass(frozen=True)
class StructuredResult:
    document: EnvelopeDocument = field(repr=False)
    model_ref: str
    input_tokens: int
    output_tokens: int
    cost: Decimal | None


@dataclass(frozen=True)
class SecretValue:
    """Explicit unwrap for adapters; never serialize/log this DTO."""

    value: bytes = field(repr=False)
