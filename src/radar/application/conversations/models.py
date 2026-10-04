"""Internal conversation intents. Not worker wire schemas or effect authority."""
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Protocol

from radar.domain.core import utc
from radar.ports.types import BlobPointer, LedgerState


class ConversationError(ValueError):
    """Static reason only; never external text, phone, name or credentials."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    return sha256(canonical(value).encode()).hexdigest()


def ref(value):
    return (isinstance(value, str) and len(value) <= 129
            and re.fullmatch(r'[a-z][a-z0-9_]{0,31}:[A-Za-z0-9_-]*[A-Za-z][A-Za-z0-9_-]*', value))


@dataclass(frozen=True)
class Channel:
    name: str
    backend: str
    enabled: bool = False
    fixture_only: bool = True

    def __post_init__(self):
        if any(not isinstance(v, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', v) for v in (self.name, self.backend)):
            raise ConversationError('invalid_channel')
        if type(self.enabled) is not bool or type(self.fixture_only) is not bool:
            raise ConversationError('invalid_channel')


@dataclass(frozen=True)
class Account:
    account_ref: str
    channel: str
    backend: str
    self_recipient_ref: str
    session_ref: str
    session_version: int
    expires_at: datetime


@dataclass(frozen=True)
class DraftInput:
    chat_refs: tuple[str, ...]
    text: str = field(repr=False)
    purpose_ref: str


@dataclass(frozen=True)
class Batch:
    batch_ref: str
    owner_ref: str
    actor_ref: str
    account_ref: str
    version: int
    status: str
    content_hash: str
    private_ref: BlobPointer
    operation_ids: tuple[str, ...]
    expires_at: datetime
    replayed: bool = False


@dataclass(frozen=True)
class ApprovalScreen:
    batch_ref: str
    version: int
    content_hash: str
    callback_ref: str
    rows: tuple[dict, ...] = field(repr=False)


@dataclass(frozen=True)
class Incoming:
    chat_ref: str
    recipient_ref: str
    provider_message_ref: str
    private_ref: BlobPointer


@dataclass(frozen=True)
class IncomingPage:
    messages: tuple[Incoming, ...]
    next_ref: BlobPointer | None = None


@dataclass(frozen=True)
class Delivery:
    operation_id: str
    owner_ref: str
    account: Account
    chat_ref: str
    recipient_ref: str
    purpose_ref: str
    content_hash: str
    text: str = field(repr=False)
    identity: dict = field(repr=False)
    provider_message_ref: str


@dataclass(frozen=True)
class DispatchResult:
    operation_id: str
    state: LedgerState
    replayed: bool = False


class ConversationVault(Protocol):
    """Authenticated, owner-bound, immutable encryption is a deployment gate."""
    def seal(self, *, owner_ref: str, plaintext: bytes) -> BlobPointer: ...
    def open(self, *, owner_ref: str, pointer: BlobPointer) -> bytes: ...


class InboxBoundary(Protocol):
    fixture_only: bool
    def sync(self, *, owner_ref: str, account: Account, enabled_chat_refs: tuple[str, ...],
             cursor_ref: BlobPointer | None, limit: int) -> IncomingPage: ...


class FixtureDispatcher(Protocol):
    fixture_only: bool
    def send_fixture(self, delivery: Delivery) -> bool: ...


class ConversationRepository(Protocol):
    def list_chats(self, *, authority, account_ref, now, limit=20, after_ref=None): ...
    def sync(self, *, authority, account_ref, now, worker, cursor_ref=None, limit=20): ...
    def list_messages(self, *, authority, account_ref, chat_ref, now, limit=20, after_ref=None): ...
    def compose(self, *, authority, event_ref, account_ref, drafts, now, expires_at): ...
    def screen(self, *, authority, batch_ref, now): ...
    def approve(self, *, authority, event_ref, callback_ref, content_hash, operation_ids, display_hash, now): ...
    def correct(self, *, authority, batch_ref, expected_version, drafts, now): ...
    def dispatch_fixture(self, *, authority, operation_id, now, dispatcher): ...
    def results(self, *, authority, batch_ref, now): ...
    def reconcile(self, *, authority, operation_id, proof_ref, now): ...


def validate_drafts(drafts):
    if not isinstance(drafts, tuple) or not 1 <= len(drafts) <= 25:
        raise ConversationError('batch_budget_exhausted')
    total = 0
    for draft in drafts:
        if not isinstance(draft, DraftInput) or not isinstance(draft.chat_refs, tuple):
            raise ConversationError('invalid_draft')
        if len(draft.chat_refs) != 1:
            raise ConversationError('recipient_ambiguous')
        if not ref(draft.chat_refs[0]) or not ref(draft.purpose_ref):
            raise ConversationError('invalid_reference')
        if not isinstance(draft.text, str) or not draft.text.strip() or len(draft.text.encode()) > 4096:
            raise ConversationError('text_budget_exhausted')
        total += len(draft.text.encode())
    if total > 32_768:
        raise ConversationError('batch_budget_exhausted')


def active(authority, now):
    if utc(authority.expires_at) <= utc(now):
        raise ConversationError('authority_expired')
