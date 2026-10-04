"""Synthetic private Telegram UI with durable intent dedupe, no HTTP transport."""
from radar.ports.types import ConditionalConflict, UIMessage


def report_text(report):
    rows = [f'run={report.operation_id} result={report.message_id}',
            f'fuente={report.source_status} error={report.source_error} registros={report.records_observed}',
            f'trabajos={report.jobs_used} paginas={report.pages_used} costoUSD={report.compute_cost_usd}',
            f'comparaciones={report.comparisons_used} limite={report.comparisons_limited}',
            f'descartes={report.discarded} ganancias_realizadas={report.realized_profit}']
    for candidate in report.candidates:
        economics = candidate.economics
        rows.append(f'{candidate.acquisition_id}/{candidate.comparable_id} banda={candidate.priority.band} '
                    f'ganancia_estimada={economics.profit} faltantes={economics.missing} '
                    f'evidencia={candidate.evidence_hashes}; precio_publicado_no_es_venta')
    return '\n'.join(rows)


class FakeUI:
    def __init__(self, store, directory):
        self.store,self.directory = store,directory
        self.store.db.execute('CREATE TABLE IF NOT EXISTS ui_intents(owner TEXT,ref TEXT,text TEXT,PRIMARY KEY(owner,ref))')

    def deliver(self, *, owner_ref, intent):
        user = self.directory.by_actor(intent.actor_ref)
        if user['owner_ref'] != owner_ref or not self.directory.accepted(user):
            raise ConditionalConflict('ui_directory_binding')
        text = report_text(intent.report) if intent.report else f'operacion={intent.operation_id} estado={intent.reason}'
        self.store.db.execute('INSERT OR IGNORE INTO ui_intents VALUES(?,?,?)',(owner_ref,intent.intent_ref,text))
        return UIMessage(intent.intent_ref,intent.actor_ref)

    def messages(self, *, owner_ref):
        return tuple(r[0] for r in self.store.db.execute('SELECT text FROM ui_intents WHERE owner=? ORDER BY ref',(owner_ref,)))


def deliver_alerts(store, ui, *, owner_ref, failpoint=lambda stage: None):
    for _,intent in store.pending_alerts(owner_ref=owner_ref):
        ui.deliver(owner_ref=owner_ref,intent=intent)
        failpoint('after_ui_delivery')
        store.mark_alert(owner_ref=owner_ref,intent_ref=intent.intent_ref)
