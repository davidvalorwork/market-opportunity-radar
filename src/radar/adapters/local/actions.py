"""Explicitly authorized synthetic sender, with independent fake provider journal.

No real account/client is accepted. Local plaintext fixtures are not encrypted
sessions, and this durable journal is not a proof of a real provider protocol.
"""
from uuid import uuid4

from radar.ports.types import ConditionalConflict, LedgerState


class SimulatedSender:
    def __init__(self, store, clock, *, test_authorized=False):
        if test_authorized is not True:
            raise ValueError('explicit_test_send_authorization_required')
        self.store,self.clock = store,clock

    def send(self, *, owner_ref, operation_id, lease, failpoint=lambda stage: None):
        record = self.store.get(owner_ref=owner_ref,operation_id=operation_id)
        if record is None:
            raise ConditionalConflict('unknown_owner_action')
        if record.state in (LedgerState.DISPATCH_COMMITTED,LedgerState.SEND_UNCERTAIN,LedgerState.PROVIDER_CONFIRMED):
            if record.state == LedgerState.DISPATCH_COMMITTED:
                return self.store.finish_local(owner_ref=owner_ref,operation_id=operation_id,expected_version=record.version,
                                                target=LedgerState.SEND_UNCERTAIN,provider_message_ref=record.provider_message_ref,now=self.clock.now())
            return record
        claimed = self.store.claim_dispatch(owner_ref=owner_ref,operation_id=operation_id,expected_version=record.version,
                                            lease=lease,provider_message_ref='fixture:'+str(uuid4()),now=self.clock.now())
        # Crashes intentionally escape after durable claim; replay marks uncertain
        # and never sends. A lease is local coordination, not provider fencing.
        failpoint('after_claim')
        if not self.store.is_current(owner_ref=owner_ref,lease=lease,now=self.clock.now()):
            return self.store.finish_local(owner_ref=owner_ref,operation_id=operation_id,expected_version=claimed.version,
                                           target=LedgerState.SEND_UNCERTAIN,provider_message_ref=claimed.provider_message_ref,now=self.clock.now())
        self._provider_accept(owner_ref,claimed)
        failpoint('after_simulated_send')
        failpoint('snapshot')
        return self.store.finish_local(owner_ref=owner_ref,operation_id=operation_id,expected_version=claimed.version,
                                       target=LedgerState.PROVIDER_CONFIRMED,provider_message_ref=claimed.provider_message_ref,now=self.clock.now())

    def _provider_accept(self, owner, record):
        # This is written independently BEFORE the app result transaction. The
        # operation is identified by protocol correlation, never text/date.
        with self.store.transaction():
            self.store.db.execute('INSERT INTO provider_proofs VALUES(?,?,?,?,?,?,?,?,?)',
                                  (owner,'proof:'+str(uuid4()),record.operation_id,record.recipient_ref,record.session_ref,
                                   record.session_version,record.content_hash,record.purpose,record.provider_message_ref))

    def proof_refs(self, *, owner_ref, operation_id):
        return tuple(r[0] for r in self.store.db.execute('SELECT ref FROM provider_proofs WHERE owner=? AND op=?',(owner_ref,operation_id)))
