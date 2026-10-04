"""Product workflow without SDK, persistence, network or LLM dependencies."""
from radar.domain.core import Scenario
from radar.domain.verticals.products import calculate, match, normalize, score
from radar.ports.types import ConditionalConflict
from radar.ports.workflow import Candidate, Report, WorkflowStore, WorkerResult


def run_saved_search(store: WorkflowStore, *, owner_ref, search_ref, command, now):
    actor = command['payload']['telegram_user_ref']
    if not store.authorize_actor(owner_ref=owner_ref, actor_ref=actor):
        raise ConditionalConflict('actor_not_authorized')
    return store.start_run(owner_ref=owner_ref, search_ref=search_ref, command=command, now=now)


def handle_worker_result(store: WorkflowStore, *, owner_ref, result: WorkerResult, now):
    previous = store.projected_report(owner_ref=owner_ref, result=result)
    if previous is not None:
        return previous
    run = store.run(owner_ref=owner_ref, operation_id=result.envelope['operation_id'])
    search = store.search_for_run(owner_ref=owner_ref, operation_id=run.operation_id)
    candidates, discarded = [], list(result.discarded)
    # Page order is explicit: acquisition and destination roles arrive as
    # source fields and become named price inputs, never precomputed margins.
    signals = store.signals_for_run(owner_ref=owner_ref, operation_id=run.operation_id) + result.signals
    acquisitions = [s for s in signals if s.price.name == 'acquisition']
    comparables = [s for s in signals if s.price.name == 'sale']
    for acquisition in acquisitions:
        for comparable in comparables:
            compatibility = match(normalize(acquisition.entity), normalize(comparable.entity))
            if compatibility.status == 'conflict':
                discarded.append(f'{acquisition.signal_id}:{comparable.signal_id}:' + ','.join(compatibility.reasons))
                continue
            scenario = Scenario(
                f'{acquisition.signal_id}:{comparable.signal_id}', search.report_currency,
                acquisition.price, comparable.price, search.units_per_lot, search.lots,
                search.minimum_lots, search.units_per_lot * search.lots,
                search.incoming, search.outgoing, search.required_incoming,
                search.required_outgoing, search.cost_plan_source, run.started_at,
            )
            economics = calculate(scenario, search.rates)
            candidates.append(Candidate(acquisition.signal_id, comparable.signal_id, compatibility,
                                        economics, score(economics, compatibility),
                                        (acquisition.evidence.content_hash, comparable.evidence.content_hash)))
    report = Report(run.operation_id, result.envelope['message_id'], tuple(candidates),
                    tuple(discarded), result.status, result.error, len(signals),
                    run.jobs_used, run.pages_used)
    store.commit_projection(owner_ref=owner_ref, result=result, report=report, now=now)
    return report


def approve_action(store: WorkflowStore, *, owner_ref, operation_id, expected_version, approval, now):
    if not store.authorize_actor(owner_ref=owner_ref, actor_ref=approval.actor_ref):
        raise ConditionalConflict('actor_not_authorized')
    return store.approve_local(owner_ref=owner_ref, operation_id=operation_id,
                               expected_version=expected_version, approval=approval, now=now)


def reconcile_uncertain(store: WorkflowStore, *, owner_ref, operation_id, proof_ref, now):
    # The repository resolves independently recorded provider evidence. A
    # caller-supplied boolean or untrusted proof object cannot settle a send.
    return store.reconcile_proof(owner_ref=owner_ref, operation_id=operation_id,
                                 proof_ref=proof_ref, now=now)
