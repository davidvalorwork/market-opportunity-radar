import json

import pytest

from radar.adapters.local.general_fixture import build, message, OWNER, ACTOR, SECRET
from radar.adapters.telegram import consent
from radar.ports.types import BlobPointer


@pytest.fixture
def assembly(tmp_path):
    value = build(tmp_path / 'control.sqlite')
    yield value
    value['store'].close()


def voice(update_id, file_id):
    value = message(update_id, '')
    del value['message']['text']
    value['message']['voice'] = {'file_id': file_id, 'duration': 3}
    return json.dumps(value).encode()


def test_voice_note_becomes_sealed_voice_input_only_with_current_consent(assembly):
    store, directory, headers = assembly['store'], assembly['directory'], {'X-Telegram-Bot-Api-Secret-Token': SECRET}
    directory.withdraw_consent(owner_ref=OWNER, actor_ref=ACTOR)
    assert assembly['wiring'].webhook.handle_update(headers, voice(1, 'FILEID-synthetic'))['statusCode'] == 200
    assert not store.db.execute('SELECT * FROM general_inputs').fetchall()
    directory.accept_consent(ACTOR, consent.CONSENT_VERSION, assembly['clock'].now(), ('event:voiceconsent',))
    assert assembly['wiring'].webhook.handle_update(headers, voice(2, 'FILEID-synthetic'))['statusCode'] == 200
    rows = store.db.execute('SELECT pointer FROM general_inputs').fetchall()
    assert len(rows) == 1
    private = json.loads(assembly['views']['worker:task-router'].open(owner_ref=OWNER, pointer=BlobPointer(**json.loads(rows[0][0]))))
    assert private['args'] == {'text': 'FILEID-synthetic', 'route': 'voice'}
    assert 'FILEID-synthetic' not in repr(store.db.execute('SELECT * FROM general_receipts').fetchall())
