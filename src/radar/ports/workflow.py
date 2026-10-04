"""Small internal workflow values; these are not transport schemas.

Local alert/action intents will need an explicit boundary contract in AWS. They
are persisted locally and never smuggled into an unrelated v1 envelope kind.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from radar.domain.core import CostInput, FxRate, Signal
from radar.domain.verticals.products import Economics, Match, Score
from .types import Approval, EnvelopeDocument, Lease, LedgerRecord


@dataclass(frozen=True)
class SavedSearch:
    search_ref: str
    query: str
    actor_ref: str
    source_ref: str
    report_currency: str
    incoming: tuple[CostInput, ...]
    outgoing: tuple[CostInput, ...]
    required_incoming: tuple[str, ...]
    required_outgoing: tuple[str, ...]
    cost_plan_source: str
    rates: tuple[FxRate, ...] = ()
    units_per_lot: int = 1
    lots: int = 1
    minimum_lots: int = 1
    max_pages: int = 1
    max_jobs: int = 1
    page_size: int = 10


@dataclass(frozen=True)
class RunState:
    operation_id: str
    search_ref: str
    actor_ref: str
    status: str
    cursor: int
    jobs_used: int
    pages_used: int
    version: int
    command_message_id: str
    started_at: datetime


@dataclass(frozen=True)
class WorkerResult:
    """Adapter-validated result with evidence-backed domain signals."""
    envelope: EnvelopeDocument
    content_hash: str
    signals: tuple[Signal, ...]
    discarded: tuple[str, ...]
    source_ref: str
    status: str
    error: str | None
    next_cursor: int | None


@dataclass(frozen=True)
class Candidate:
    acquisition_id: str
    comparable_id: str
    compatibility: Match
    economics: Economics
    priority: Score
    evidence_hashes: tuple[str, ...]


@dataclass(frozen=True)
class Report:
    operation_id: str
    message_id: str
    candidates: tuple[Candidate, ...]
    discarded: tuple[str, ...]
    source_status: str
    source_error: str | None
    records_observed: int
    jobs_used: int
    pages_used: int
    compute_cost_usd: str | None = None
    realized_profit: str | None = None


@dataclass(frozen=True)
class LocalAlertIntent:
    intent_ref: str
    actor_ref: str
    operation_id: str
    reason: str
    report: Report | None = None


class WorkflowStore(Protocol):
    def save_search(self, *, owner_ref: str, search: SavedSearch) -> None: ...
    def search(self, *, owner_ref: str, search_ref: str) -> SavedSearch: ...
    def search_for_run(self, *, owner_ref: str, operation_id: str) -> SavedSearch: ...
    def start_run(self, *, owner_ref: str, search_ref: str, command: EnvelopeDocument,
                  now: datetime) -> RunState: ...
    def run(self, *, owner_ref: str, operation_id: str) -> RunState: ...
    def projected_report(self, *, owner_ref: str, result: WorkerResult) -> Report | None: ...
    def signals_for_run(self, *, owner_ref: str, operation_id: str) -> tuple[Signal, ...]: ...
    def commit_projection(self, *, owner_ref: str, result: WorkerResult,
                          report: Report, now: datetime) -> bool: ...
    def authorize_actor(self, *, owner_ref: str, actor_ref: str) -> bool: ...
    def approve_local(self, *, owner_ref: str, operation_id: str,
                      expected_version: int, approval: Approval, now: datetime) -> LedgerRecord: ...
    def reconcile_proof(self, *, owner_ref: str, operation_id: str,
                        proof_ref: str, now: datetime) -> LedgerRecord: ...
