"""Legal transitions; no external send or automatic uncertain retry."""

from dataclasses import replace
from datetime import datetime

from .core import Opportunity, ProposedAction, utc


OPPORTUNITY_TRANSITIONS = {
    "candidate": frozenset({"pending_review", "rejected", "archived"}),
    "pending_review": frozenset({"accepted", "rejected", "archived"}),
    "accepted": frozenset({"archived"}),
    "rejected": frozenset({"archived"}),
    "archived": frozenset(),
}
ACTION_TRANSITIONS = {
    "proposed": frozenset({"approved", "cancelled"}),
    "approved": frozenset({"dispatch_committed", "cancelled"}),
    "dispatch_committed": frozenset({"provider_confirmed", "send_uncertain"}),
    "send_uncertain": frozenset({"provider_confirmed"}),
    "provider_confirmed": frozenset(),
    "cancelled": frozenset(),
}


def transition_opportunity(value: Opportunity, target: str, *, human_reviewed: bool = False) -> Opportunity:
    if target not in OPPORTUNITY_TRANSITIONS[value.state]:
        raise ValueError("illegal_opportunity_transition")
    if target == "accepted" and human_reviewed is not True:
        raise ValueError("human_review_required")
    return replace(value, state=target)


def transition_action(value: ProposedAction, target: str, *, at: datetime,
                      approved_action: ProposedAction | None = None,
                      provider_evidence: bool = False) -> ProposedAction:
    """Approval must bind the exact immutable action (including expiry/version).

Ledger/lease atomicity belongs to a port/application transaction. This pure
guard is not provider fencing, a durable approval store, or authorization alone.
"""
    utc(at)
    if target not in ACTION_TRANSITIONS[value.state]:
        raise ValueError("illegal_action_transition")
    if target in {"approved", "dispatch_committed"}:
        if approved_action != value or at >= value.expires_at:
            raise ValueError("matching_unexpired_approval_required")
    if target == "provider_confirmed" and provider_evidence is not True:
        raise ValueError("provider_evidence_required")
    return replace(value, state=target)
