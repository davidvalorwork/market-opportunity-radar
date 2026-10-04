from dataclasses import replace
import json

import pytest

from radar.adapters.local.runtime import LocalRuntime
from radar.adapters.local.telegram import consent
from radar.adapters.telegram.allowlist import PhoneAllowlist
from radar.ports.types import ConditionalConflict
from conftest import ACTOR,AT,FIXTURES,OWNER,update


def callback(runtime, *, numeric, update_id, data):
    body=json.dumps({'update_id':update_id,'callback_query':{'id':'fixture-query','from':{'id':numeric},'data':data}}).encode()
    return runtime.webhook.handle_update({'X-Telegram-Bot-Api-Secret-Token':'synthetic-local-secret'},body)


@pytest.mark.parametrize('command',['/start','/mis_datos','/borrar','/stop'])
def test_current_webhook_ungated_commands_persist_without_consent(runtime,command):
    runtime.directory.enroll_synthetic(202,owner_ref=OWNER,actor_ref='user:beta')
    result=update(runtime,numeric=202,update_id=2,command=command)
    assert result['statusCode']==200
    stored=runtime.store.command(owner_ref=OWNER,operation_id=runtime.bridge.last_accepted.operation_id)
    assert stored['payload']['command']==command[1:]
    if command=='/start':
        assert 'Condiciones del radar' in result['body']


def test_current_webhook_consent_gates_search_then_durably_accepts(runtime):
    runtime.directory.enroll_synthetic(202,owner_ref=OWNER,actor_ref='user:beta')
    assert update(runtime,numeric=202,update_id=2)['statusCode']==200
    assert runtime.bridge.last_accepted is None
    assert not runtime.store.pending(owner_ref=OWNER,limit=10).entries
    response=callback(runtime,numeric=202,update_id=3,data=consent.ACCEPT)
    assert response['statusCode']==200 and runtime.directory.get(202)['consent_version']==consent.CONSENT_VERSION
    assert callback(runtime,numeric=202,update_id=4,data=consent.ACCEPT)['statusCode']==200
    assert runtime.store.db.execute('SELECT count(*) FROM consent_history WHERE actor=?',('user:beta',)).fetchone()[0]==1
    assert update(runtime,numeric=202,update_id=5)['statusCode']==200
    assert runtime.bridge.last_accepted is not None


@pytest.mark.parametrize('boundary',['before_command','before_worker'])
def test_consent_revocation_after_enqueue_prevents_new_work(runtime,boundary):
    assert update(runtime)['statusCode']==200
    runtime.relay(OWNER)
    if boundary=='before_worker':
        runtime.process_commands(OWNER,search_ref='search:perfume')
        runtime.relay(OWNER)
    runtime.directory.withdraw_consent(owner_ref=OWNER,actor_ref=ACTOR)
    if boundary=='before_command':
        runtime.process_commands(OWNER,search_ref='search:perfume')
    else:
        runtime.process_workers(OWNER)
    assert runtime.store.db.execute('SELECT count(*) FROM quarantine WHERE owner=?',(OWNER,)).fetchone()[0]==1
    assert runtime.worker.executions==0


def test_replay_original_run_ignores_mutable_search_actor_after_application_before_ack(runtime):
    update(runtime)
    op=runtime.bridge.last_accepted.operation_id
    runtime.relay(OWNER)
    def crash(stage):
        raise RuntimeError(stage)
    with pytest.raises(RuntimeError):
        runtime.process_commands(OWNER,search_ref='search:perfume',failpoint=crash)
    run=runtime.store.run(owner_ref=OWNER,operation_id=op)
    runtime.directory.enroll_synthetic(202,owner_ref=OWNER,actor_ref='user:beta')
    runtime.directory.accept_consent('user:beta',consent.CONSENT_VERSION,AT,('beta-consent',))
    search=runtime.store.search(owner_ref=OWNER,search_ref='search:perfume')
    runtime.store.save_search(owner_ref=OWNER,search=replace(search,actor_ref='user:beta'))
    runtime.queue.replay(owner_ref=OWNER,destination='commands.fifo')
    runtime.process_commands(OWNER,search_ref='search:perfume')
    assert runtime.store.run(owner_ref=OWNER,operation_id=op)==run
    runtime.pump(OWNER,search_ref='search:perfume')
    assert runtime.store.reports(owner_ref=OWNER,operation_id=op)


def test_contact_enrollment_is_explicit_synthetic_atomic_and_does_not_store_phone(tmp_path,caplog):
    phones=PhoneAllowlist.from_json('{"schema_version":1,"entries":[{"phone_e164":"+10000000000","role":"owner"}]}')
    rt=LocalRuntime(tmp_path/'contact.sqlite',fixtures=FIXTURES,synthetic_authorized=True,
                    phone_allowlist=phones,synthetic_contact_owner_ref=OWNER)
    body=json.dumps({'update_id':1,'message':{'chat':{'id':303,'type':'private'},'from':{'id':303},
                     'contact':{'user_id':303,'phone_number':'+10000000000'}}}).encode()
    def crash(stage):
        if stage=='after_enrollment_receipt':
            raise RuntimeError(stage)
    rt.store.failpoint=crash
    assert rt.webhook.handle_update({'X-Telegram-Bot-Api-Secret-Token':'synthetic-local-secret'},body)['statusCode']==500
    assert rt.directory.get(303) is None
    assert rt.store.db.execute('SELECT count(*) FROM ignored').fetchone()[0]==0
    rt.store.failpoint=lambda stage:None
    with caplog.at_level('INFO'):
        assert rt.webhook.handle_update({'X-Telegram-Bot-Api-Secret-Token':'synthetic-local-secret'},body)['statusCode']==200
    user=rt.directory.get(303)
    assert user['owner_ref']==OWNER and user['role']=='owner' and user['consent_version'] is None
    assert '+10000000000' not in caplog.text
    rows=rt.store.db.execute('SELECT * FROM directory').fetchall()
    assert '+10000000000' not in repr(rows)
    rt.store.close()


def test_contact_enroll_is_disabled_without_explicit_synthetic_owner(runtime):
    with pytest.raises(ConditionalConflict):
        runtime.directory.enroll(303,'owner',AT,('untrusted-enroll',))
    assert runtime.directory.get(303) is None


def test_run_comparison_budget_visible_and_deterministic(runtime):
    runtime.worker.fixtures=tuple(dict(FIXTURES[0],id=f'buy{i}') for i in range(3))+tuple(dict(FIXTURES[1],id=f'sell{i}') for i in range(3))
    search=runtime.store.search(owner_ref=OWNER,search_ref='search:perfume')
    runtime.store.save_search(owner_ref=OWNER,search=replace(search,max_comparisons=2))
    update(runtime)
    op=runtime.bridge.last_accepted.operation_id
    runtime.pump(OWNER,search_ref=search.search_ref)
    report=runtime.store.reports(owner_ref=OWNER,operation_id=op)[0]
    assert report.comparisons_used==len(report.candidates)==2
    assert report.comparisons_limited and 'comparison_budget_exhausted' in report.discarded
    assert report.evaluated_pairs==('buy0:sell0','buy0:sell1')
