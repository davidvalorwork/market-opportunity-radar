"""No-I/O worker that reads ONLY explicitly provided public synthetic fixtures."""
from dataclasses import replace
from hashlib import sha256
from uuid import uuid4

from radar.domain.core import CostInput, Entity, Evidence, Knowledge, Money, Signal
from radar.domain.verticals.products import normalize
from radar.ports.types import BlobPointer, ConditionalConflict
from radar.ports.workflow import WorkerResult
from .sqlite import canonical, digest, parse, stamp
from .wire import validate_envelope


def decode_result(store, *, owner_ref, envelope):
    validate_envelope(envelope)
    if envelope['owner_ref'] != owner_ref or envelope['kind'] != 'browser.result':
        raise ConditionalConflict('result_owner_or_kind')
    task,_,next_cursor = store.task(owner_ref=owner_ref,message_id=envelope.get('causation_id'))
    run = store.run(owner_ref=owner_ref,operation_id=task['operation_id'])
    search = store.search_for_run(owner_ref=owner_ref,operation_id=run.operation_id)
    signals, discarded = [],[]
    for record in envelope['payload']['records']:
        fields, evidence = record['fields'],record['evidence']
        # Hash binds extracted fields to the independently retained fixture bytes.
        pointer = BlobPointer('evidence/'+evidence['content_hash'],evidence['content_hash'])
        if store.read(owner_ref=owner_ref,blob=pointer,max_bytes=16384) != canonical(fields):
            raise ConditionalConflict('evidence_fields_mismatch')
        try:
            role = fields['role']
            if role not in ('acquisition','sale'):
                raise ValueError('unsupported_record_role')
            entity = normalize(Entity(fields['id'],fields.get('brand'),fields.get('model'),
                               tuple((k[len('variant_'):],v) for k,v in fields.items() if k.startswith('variant_')),
                               fields.get('condition','unknown'),fields.get('unit'),fields.get('authenticity','unknown')))
            observed = parse(evidence['observed_at'])
            amount = Money(fields['amount'],fields['currency']) if 'amount' in fields else None
            price = CostInput(role,amount,Knowledge.KNOWN if amount else Knowledge.UNKNOWN,
                              evidence['url'],observed,role=='acquisition')
            signals.append(Signal(fields['id'],entity,'sell_listing',price,
                                  Evidence(evidence['url'],observed,evidence['content_hash'])))
        except (KeyError,ValueError) as exc:
            discarded.append(fields.get('id','unknown')+':'+(str(exc) if isinstance(exc,ValueError) else 'missing_field'))
    return WorkerResult(envelope,digest(envelope),tuple(signals),tuple(discarded),search.source_ref,
                        envelope['payload']['status'],envelope['payload'].get('error',{}).get('code'),next_cursor)


class FakeWorker:
    def __init__(self, store, clock, *, fixtures, synthetic_authorized=False):
        if synthetic_authorized is not True:
            raise ValueError('explicit_synthetic_fixture_authorization_required')
        self.store,self.clock = store,clock
        # A bounded fixture set, not a crawler or network source.
        if len(fixtures) > 1000:
            raise ValueError('bounded_fixture_set')
        self.fixtures = tuple(dict(f) for f in fixtures)
        self.executions = 0

    def execute(self, *, owner_ref, envelope):
        validate_envelope(envelope)
        task,offset,_ = self.store.task(owner_ref=owner_ref,message_id=envelope['message_id'])
        if task != envelope or envelope['owner_ref'] != owner_ref:
            raise ConditionalConflict('worker_task_binding')
        original = self.store.worker_result(owner_ref=owner_ref,task_message_id=task['message_id'])
        if original:
            return original
        self.store._active(owner_ref)
        run = self.store.run(owner_ref=owner_ref,operation_id=task['operation_id'])
        search = self.store.search_for_run(owner_ref=owner_ref,operation_id=run.operation_id)
        if run.status != 'running' or run.version != task['expected_version'] or not self.store.current_consent(owner_ref,run.actor_ref) or not self.store.authorize_actor(owner_ref=owner_ref,actor_ref=run.actor_ref) or not self.store.source_allowed(owner_ref=owner_ref,source_ref=search.source_ref) or parse(task['deadline']) <= self.clock.now():
            raise ConditionalConflict('worker_cancelled_unauthorized_or_expired')
        batch = envelope['payload']['batch']
        records = []
        for fields in self.fixtures[offset:offset+batch]:
            data = canonical(fields)
            content_hash = sha256(data).hexdigest()
            self.store.put_if_absent(owner_ref=owner_ref,blob=BlobPointer('evidence/'+content_hash,content_hash),content=data)
            records.append({'fields':fields,'evidence':{'url':'https://fixtures.example/public/'+fields.get('id','unknown'),
                                                       'observed_at':stamp(run.started_at),'content_hash':content_hash}})
        self.executions += 1
        result = dict(task,message_id=str(uuid4()),kind='browser.result',causation_id=task['message_id'],
                      payload={'schema_version':1,'status':'succeeded','records':records,
                               'timings':{'total_ms':0},'versions':{'fixture':'1'}})
        return self.store.complete_worker(owner_ref=owner_ref,task_message_id=task['message_id'],envelope=result,
                                           next_cursor=offset+batch if offset+batch<len(self.fixtures) else None,now=self.clock.now())
