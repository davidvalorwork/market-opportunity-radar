import json
import os
from pathlib import Path
import subprocess
import sys

from radar.adapters.local.polling import PollingError
from radar.adapters.telegram.bot_api import BotApi
from radar.entrypoints.local_telegram import load_factory, main
from test_polling import BotTransport, FakePrivateVault, PHONE, SECRET, TOKEN, build, message


def configuration(tmp_path):
    private=tmp_path/'.local'
    private.mkdir()
    phones=private/'allowlist.json'
    phones.write_text(json.dumps({'schema_version':1,'entries':[{'phone_e164':PHONE,'role':'owner'}]}),encoding='utf-8')
    return ['--factory','trusted.config:build','--state-db',str(private/'control.sqlite'),
            '--allowlist',str(phones),'--bot-ref','bot:local','--max-polls','1']


def test_help_works_as_real_module_without_secret_or_factory():
    root=Path(__file__).resolve().parents[2]
    env=dict(os.environ,PYTHONPATH=str(root/'src')+os.pathsep+str(root),PYTHONDONTWRITEBYTECODE='1')
    result=subprocess.run([sys.executable,'-m','radar.entrypoints.local_telegram','--help'],
        cwd=root,env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==0
    assert '--take-over-bot' in result.stdout and '--authorize-bot-io' in result.stdout
    assert TOKEN not in result.stdout+result.stderr


def test_no_io_authorization_fails_before_factory_or_api(tmp_path,capsys):
    def forbidden(*args,**kwargs):
        raise AssertionError('must not construct I/O')
    assert main(configuration(tmp_path),environ={},factory_loader=forbidden,api_factory=forbidden)==2
    assert capsys.readouterr().err.strip()=='bot_io_not_authorized'


def test_missing_secrets_fail_before_factory_or_takeover(tmp_path,capsys):
    def forbidden(*args,**kwargs):
        raise AssertionError('must not construct I/O')
    args=configuration(tmp_path)+['--authorize-bot-io','--take-over-bot']
    assert main(args,environ={},factory_loader=forbidden,api_factory=forbidden)==2
    assert capsys.readouterr().err.strip()=='secret_configuration_required'


def test_factory_failure_is_static_without_secret_or_private_text(tmp_path,capsys):
    def fail(spec):
        raise RuntimeError(f'{TOKEN} {PHONE} sensitive text')
    assert main(configuration(tmp_path)+['--authorize-bot-io'],
        environ={'RADAR_TELEGRAM_TOKEN':TOKEN,'RADAR_TELEGRAM_SECRET':SECRET},factory_loader=fail)==2
    captured=capsys.readouterr()
    assert captured.err.strip()=='local_polling_failed' and not captured.out


def test_factory_cannot_use_polling_error_to_emit_private_text(tmp_path,capsys):
    def fail(spec):
        raise PollingError(f'{TOKEN} {PHONE} private topic')
    assert main(configuration(tmp_path)+['--authorize-bot-io'],
        environ={'RADAR_TELEGRAM_TOKEN':TOKEN,'RADAR_TELEGRAM_SECRET':SECRET},factory_loader=fail)==2
    assert capsys.readouterr().err.strip()=='local_polling_failed'


def test_configured_fake_transport_runs_and_closes_store(tmp_path,capsys):
    transport,vault=BotTransport([message()]),FakePrivateVault()
    state={}
    def factory(*,state_db,phone_allowlist,secret):
        runner,directory=build(state_db,vault,transport,secret=secret)
        state['store']=runner.wiring.store
        return runner.wiring
    args=configuration(tmp_path)+['--authorize-bot-io']
    assert main(args,environ={'RADAR_TELEGRAM_TOKEN':TOKEN,'RADAR_TELEGRAM_SECRET':SECRET},
        factory_loader=lambda spec:factory,api_factory=lambda token,timeout:BotApi(token,transport=transport,timeout=timeout))==0
    assert len(transport.sent)==1 and capsys.readouterr().err==''
    import sqlite3
    try:
        state['store'].db.execute('SELECT 1')
    except sqlite3.ProgrammingError:
        pass
    else:
        raise AssertionError('store not closed')


def test_missing_private_wiring_prevents_even_takeover(tmp_path,capsys):
    transport,vault=BotTransport(webhook='https://example.invalid/old'),FakePrivateVault()
    vault.available=False
    def factory(*,state_db,phone_allowlist,secret):
        return build(state_db,vault,transport,secret=secret)[0].wiring
    assert main(configuration(tmp_path)+['--authorize-bot-io','--take-over-bot'],
        environ={'RADAR_TELEGRAM_TOKEN':TOKEN,'RADAR_TELEGRAM_SECRET':SECRET},factory_loader=lambda spec:factory,
        api_factory=lambda token,timeout:BotApi(token,transport=transport,timeout=timeout))==2
    assert capsys.readouterr().err.strip()=='private_backend_unavailable' and transport.calls==[]


def test_factory_binding_mismatch_prevents_api_construction(tmp_path,capsys):
    transport,vault=BotTransport(),FakePrivateVault()
    def factory(*,state_db,phone_allowlist,secret):
        return build(state_db,vault,transport,secret='wrong-secret')[0].wiring
    def forbidden(*args,**kwargs):
        raise AssertionError('must not construct I/O')
    assert main(configuration(tmp_path)+['--authorize-bot-io'],
        environ={'RADAR_TELEGRAM_TOKEN':TOKEN,'RADAR_TELEGRAM_SECRET':SECRET},factory_loader=lambda spec:factory,
        api_factory=forbidden)==2
    assert capsys.readouterr().err.strip()=='factory_binding'


def test_factory_path_must_be_operator_configuration():
    for spec in ('','missing_separator','module:function:extra'):
        try:
            load_factory(spec)
        except PollingError as error:
            assert error.code=='invalid_factory'
        else:
            raise AssertionError('invalid factory accepted')
