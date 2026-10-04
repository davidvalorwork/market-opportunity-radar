from datetime import datetime, timezone
import json

import pytest

from radar.adapters.local.runtime import FixedClock, LocalRuntime
from radar.adapters.local.telegram import consent
from radar.domain.core import CostInput, Knowledge, Money
from radar.ports.workflow import SavedSearch

OWNER = 'owner:alpha'
ACTOR = 'user:alpha'
AT = datetime(2026,10,3,tzinfo=timezone.utc)


def record(ref, role, amount, *, volume='100', brand='Synthetic'):
    return {'id':ref,'role':role,'amount':amount,'currency':'USD','brand':brand,
            'model':'Fixture','variant_volume_ml':volume,'condition':'new',
            'unit':'bottle','authenticity':'original_declared'}


FIXTURES = (record('source','acquisition','50'),record('destination','sale','90'),
            record('wrong-size','sale','150',volume='30'))


def configure(runtime, *, owner=OWNER, actor=ACTOR, numeric=101, pages=1, jobs=1, page_size=10, authorized=True, incoming=None):
    runtime.directory.enroll_synthetic(numeric,owner_ref=owner,actor_ref=actor)
    runtime.directory.accept_consent(actor,consent.CONSENT_VERSION if consent else 'synthetic-v1',AT,('consent:'+actor,))
    if incoming is None:
        incoming = (CostInput('shipping',Money('10','USD'),Knowledge.ESTIMATED,'synthetic quote',AT,True),)
    search = SavedSearch('search:perfume','fixture',actor,'source:public','USD',incoming,(),
                         ('shipping',),(), 'explicit synthetic full-lot plan',max_pages=pages,
                         max_jobs=jobs,page_size=page_size)
    runtime.store.save_search(owner_ref=owner,search=search)
    runtime.store.allow_source(owner_ref=owner,source_ref=search.source_ref,authorized=authorized)
    return search


def update(runtime, *, numeric=101, update_id=1, command='/buscar fixture'):
    body = json.dumps({'update_id':update_id,'message':{'chat':{'id':numeric,'type':'private'},
                      'from':{'id':numeric},'text':command}}).encode()
    return runtime.webhook.handle_update({'X-Telegram-Bot-Api-Secret-Token':'synthetic-local-secret'},body)


@pytest.fixture
def runtime(tmp_path):
    value = LocalRuntime(tmp_path/'control.sqlite',fixtures=FIXTURES,synthetic_authorized=True,clock=FixedClock(AT))
    configure(value)
    yield value
    value.store.close()
