"""Synchronous injectable ports. No SDKs, storage or external effects here.

Every sensitive operation has an explicit opaque owner scope. Implementations
must authorize it; annotations and runtime structural checks are not security.
All datetimes are aware UTC. Boundary adapters validate the canonical B1 schemas.
"""

from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from .types import (
    AcceptedCommand, Approval, BlobPointer, Capability, DatedRate,
    EnvelopeDocument, InlineButton, JSONValue, Lease, LedgerRecord, LedgerState,
    OutboxEntry, OwnerScope, PendingOutbox, QueueDelivery, Receipt,
    SecretValue, StructuredRequest, StructuredResult, UIMessage,
)


@runtime_checkable
class UnitOfWork(Protocol):
    """Atomic control commits; no queue/provider I/O inside a transaction."""

    def accept_command(self, *, owner_ref: OwnerScope, receipt: Receipt,
                       command: EnvelopeDocument, outbox: OutboxEntry) -> AcceptedCommand:
        """Commit receipt+command+outbox together, or nothing.

        Same owner/channel/event and command hash returns the ORIGINAL IDs with
        replayed=True. Mismatching hash raises ConditionalConflict, never rewrites
        an accepted command. Envelope IDs and outbox command must match.
        """
        ...

    def approve_action(self, *, owner_ref: OwnerScope, operation_id: str,
                       expected_version: int, approval: Approval,
                       outbox: OutboxEntry, now: datetime) -> LedgerRecord:
        """CAS proposed->approved + child outbox atomically; bind approval.

        Expired/cross-owner approvals or stale version raise ConditionalConflict.
        Duplicate receipt/callback must not publish another operation.
        """
        ...

    def claim_dispatch(self, *, owner_ref: OwnerScope, operation_id: str,
                       expected_version: int, lease: Lease,
                       provider_message_ref: str, now: datetime) -> LedgerRecord:
        """Atomically check live lease/current approval and approved->dispatch_committed.

        Persist protocol correlation BEFORE provider I/O. Only one claimant
        succeeds; any dispatch_committed/send_uncertain replay cannot send again.
        Failure raises ConditionalConflict. This is NOT fencing at the provider.
        """
        ...

    def record_result(self, *, owner_ref: OwnerScope, operation_id: str,
                      expected_version: int, target: LedgerState,
                      outbox: OutboxEntry, provider_message_ref: str | None,
                      now: datetime) -> LedgerRecord:
        """Atomically CAS ledger+result outbox before acknowledging delivery.

        dispatch_committed -> provider_confirmed|send_uncertain only. Reconcile
        send_uncertain->provider_confirmed only with independently verified proof;
        never restore approved or produce a send retry. Repeated commits with
        identical identity/result converge; conflicting evidence is a conflict.
        """
        ...


@runtime_checkable
class Outbox(Protocol):
    def pending(self, *, owner_ref: OwnerScope, limit: int,
                cursor: str | None = None) -> PendingOutbox:
        """Bounded indexed pagination, no global scan and no unbounded batch."""
        ...

    def mark_published(self, *, owner_ref: OwnerScope, message_id: str,
                       expected_version: int, published_at: datetime) -> bool:
        """CAS only after queue acceptance. Crash before mark may republish.

        Never TTL-delete unpublished work. Consumers remain idempotent beyond
        transport deduplication windows. False means already marked/stale.
        """
        ...


@runtime_checkable
class Ledger(Protocol):
    def get(self, *, owner_ref: OwnerScope, operation_id: str) -> LedgerRecord | None: ...

    def propose(self, *, owner_ref: OwnerScope, operation_id: str,
                content_hash: str, recipient_ref: str, purpose: str,
                session_ref: str, session_version: int) -> LedgerRecord:
        """Conditional insert; identical replay returns original, conflict raises."""
        ...

    def transition(self, *, owner_ref: OwnerScope, operation_id: str,
                   expected_version: int, expected_state: LedgerState,
                   target: LedgerState, outbox: OutboxEntry, now: datetime,
                   provider_message_ref: str | None = None) -> LedgerRecord:
        """Atomic CAS completion+result outbox; approve/dispatch MUST use UoW.

        Allowed: dispatch_committed->confirmed|uncertain; uncertain->confirmed
        with proof validated by caller. No transition back to approved/proposed.
        State mismatch or stale version raises ConditionalConflict. May delegate
        to UnitOfWork.record_result; never implement as two separate writes.
        """
        ...


@runtime_checkable
class LeaseStore(Protocol):
    def acquire(self, *, owner_ref: OwnerScope, session_ref: str, worker_ref: str,
                now: datetime, ttl: timedelta) -> Lease | None:
        """Conditional acquisition; check expiry explicitly, not deferred TTL cleanup."""
        ...

    def renew(self, *, owner_ref: OwnerScope, lease: Lease, now: datetime,
              ttl: timedelta) -> Lease | None:
        """Only live token+worker+version can renew; no resurrection after expiry."""
        ...

    def is_current(self, *, owner_ref: OwnerScope, lease: Lease, now: datetime) -> bool: ...

    def release(self, *, owner_ref: OwnerScope, lease: Lease) -> bool:
        """Conditional token/version release, never release another worker's lease."""
        ...


@runtime_checkable
class BlobStore(Protocol):
    def put_if_absent(self, *, owner_ref: OwnerScope, blob: BlobPointer,
                      content: bytes) -> BlobPointer:
        """Immutable owner-authorized write; verify digest, reject different content."""
        ...

    def read(self, *, owner_ref: OwnerScope, blob: BlobPointer, max_bytes: int) -> bytes:
        """Verify size/digest/ownership; private content is still ciphertext."""
        ...


@runtime_checkable
class TaskQueue(Protocol):
    def publish(self, *, owner_ref: OwnerScope, entry: OutboxEntry) -> str:
        """Validated envelope only, owner must match; returns acceptance reference."""
        ...

    def receive(self, *, owner_ref: OwnerScope, destination: str,
                limit: int) -> tuple[QueueDelivery, ...]: ...

    def acknowledge(self, *, owner_ref: OwnerScope, delivery: QueueDelivery) -> None:
        """Only after durable result/outbox; uncertainty is NOT a retryable send."""
        ...


@runtime_checkable
class UserInterface(Protocol):
    def send(self, *, owner_ref: OwnerScope, conversation_ref: str, text: str,
             buttons: tuple[tuple[InlineButton, ...], ...] = ()) -> UIMessage: ...

    def edit(self, *, owner_ref: OwnerScope, conversation_ref: str, message_ref: str,
             text: str, buttons: tuple[tuple[InlineButton, ...], ...] = ()) -> UIMessage: ...

    def answer_callback(self, *, owner_ref: OwnerScope, callback_ref: str,
                        text: str | None = None, alert: bool = False) -> None: ...

    def send_document(self, *, owner_ref: OwnerScope, conversation_ref: str,
                      document: BlobPointer, filename: str,
                      caption: str | None = None) -> UIMessage: ...


@runtime_checkable
class StructuredLLM(Protocol):
    def generate(self, *, owner_ref: OwnerScope,
                 request: StructuredRequest) -> StructuredResult:
        """Explicit enabled/budget/privacy check; no paid fallback or tool authority.

        Validate output at boundary. Cache scope includes owner/privacy, model,
        prompt and schema versions, language and content hash, not text alone.
        """
        ...


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Aware UTC; adapters supply time, business code never reads wall clock."""
        ...


@runtime_checkable
class Secrets(Protocol):
    def get(self, *, owner_ref: OwnerScope, secret_ref: str) -> SecretValue:
        """Scoped resolver; no enumeration and no logging/LLM/queue serialization."""
        ...


@runtime_checkable
class Capabilities(Protocol):
    def get(self, *, owner_ref: OwnerScope, platform: str, backend: str,
            operation: str, session_ref: str | None = None) -> Capability | None:
        """Absent/documented capability is not authority; requires access permission."""
        ...


@runtime_checkable
class FxRates(Protocol):
    def get(self, *, owner_ref: OwnerScope, base_currency: str, quote_currency: str,
            as_of: datetime) -> DatedRate | None:
        """Unknown/stale is missing, not a zero or silently today's rate."""
        ...
