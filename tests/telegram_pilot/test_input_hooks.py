import json

from radar.adapters.local.general_fixture import build, message, OWNER, SECRET


def test_command_handler_replay_no_second_io(tmp_path):
    assembly = build(tmp_path/'control.sqlite')
    wiring, runtime = assembly['wiring'],assembly['runtime']
    seen = []
    runtime.input_handlers['pilot_help'] = lambda **kwargs: seen.append(kwargs['event_ref'])
    update = message(1,'/ayuda')
    # Existing fixture has authenticated enrolled/consenting owner.
    wiring.webhook.handle_update({'X-Telegram-Bot-Api-Secret-Token':SECRET},json.dumps(update).encode())
    runtime.ingest(OWNER)
    runtime.ingest(OWNER)
    assert len(seen)==1
    assembly['store'].close()


def test_hook_lease_is_bounded(tmp_path):
    assembly = build(tmp_path/'control.sqlite')
    runtime = assembly['runtime']
    assert runtime.input_lease_seconds == 60
    assembly['store'].close()
