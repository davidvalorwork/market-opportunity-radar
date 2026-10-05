"""Administrative setup/run for one local Telegram pilot; no cloud provisioning.

Secrets stay in this process. SSM access is explicit and read-only. Configuration,
identities and state are generated under an operator-only directory, not Git.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess

from radar.adapters.local.general_bindings import from_config, private_operator_path
from radar.adapters.local.polling import PollingRunner
from radar.adapters.local.private_vault import create_private_directory, _permissions
from radar.adapters.telegram.allowlist import PhoneAllowlist
from radar.adapters.telegram.bot_api import BotApi


def _write_new(path, body):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    _permissions(path)


def _allowlist_bytes(path):
    """Normalize the existing operator note, not additional enrollment fields."""
    try:
        document = json.loads(Path(path).read_text(encoding='utf-8'))
        if isinstance(document,dict) and set(document)=={'schema_version','entries','note'} and isinstance(document['note'],str):
            document = {k:document[k] for k in ('schema_version','entries')}
        body = json.dumps(document,ensure_ascii=False).encode()
        PhoneAllowlist.from_json(body.decode())
        return body
    except Exception:
        raise ValueError('pilot_allowlist_invalid') from None


def setup(*, root, allowlist_path, vault_helper, keygen_helper, whatsapp_helper):
    """One NEW leaf only; no overwriting an operator configuration or identity."""
    root = Path(root).absolute()
    allowlist_bytes = _allowlist_bytes(allowlist_path)
    allowlist = PhoneAllowlist.from_json(allowlist_bytes.decode())
    del allowlist
    for executable in (vault_helper, keygen_helper, whatsapp_helper):
        if not Path(executable).is_file():
            raise ValueError('pilot_helper_missing')
    node = shutil.which('node')
    mcporter = Path(node).parent / 'node_modules' / 'mcporter' / 'dist' / 'cli.js' if node else None
    if node is None or not mcporter.is_file():
        raise ValueError('agent_reach_cli_missing')
    create_private_directory(root)
    keys = create_private_directory(root / 'keys')
    vault_root = create_private_directory(root / 'vault')
    wa_root = create_private_directory(root / 'whatsapp')
    result = subprocess.run([str(Path(keygen_helper).absolute()), '--new', str(keys)],
        capture_output=True, timeout=10, check=False)
    if result.returncode or result.stdout or result.stderr:
        raise ValueError('pilot_key_creation_failed')
    for path in keys.iterdir():
        _permissions(path)
    config = {
        'owner_ref': 'user:pilotowner', 'timezone': 'America/Caracas',
        'allowed_timezones': ['America/Caracas'], 'max_calls': 100,
        'max_messages': 20, 'max_usd': '0', 'parser_enabled': False,
        'input_lease_seconds': 240, 'polling_lease_seconds': 300,
        'vault': {'root': str(vault_root), 'helper': str(Path(vault_helper).absolute()),
            'helper_sha256': sha256(Path(vault_helper).read_bytes()).hexdigest(),
            'identity_file': str(keys / 'identity.agekey'),
            'recipient': (keys / 'recipient.txt').read_text().strip()},
        'grants': {name: 'cap:pilot' for name in ('search', 'read', 'inform', 'compose', 'contact', 'schedule')},
        'sources': [], 'authorized_sources': [],
        'channels': [{'name': 'whatsapp', 'backend': 'local_wameow', 'enabled': True, 'fixture_only': False}],
        'backend_factory': 'radar.adapters.local.pilot_backend:build',
        'pilot': {'whatsapp_helper': str(Path(whatsapp_helper).absolute()),
            'whatsapp_helper_sha256': sha256(Path(whatsapp_helper).read_bytes()).hexdigest(),
            'session_root': str(wa_root), 'authorize_whatsapp_live': True,
            'agent_reach': {'node': str(Path(node).absolute()), 'mcporter_script':str(mcporter)},
            'research': {'max_items': 20, 'max_requests': 3, 'max_pages': 3,
                'max_bytes': 2097152, 'seconds': 60, 'retention_seconds': 604800}},
    }
    _write_new(root / 'allowlist.json', allowlist_bytes)
    _write_new(root / 'config.json', json.dumps(config, ensure_ascii=False, indent=2).encode())
    _write_new(root / 'webhook-secret.txt', secrets.token_urlsafe(32).encode())
    return {'configured': True, 'llm_enabled': False, 'private_directory': str(root)}


def _token(parameter, region):
    value = os.environ.get('RADAR_TELEGRAM_TOKEN')
    if value:
        return value
    if not parameter:
        raise ValueError('telegram_token_required')
    import boto3
    from botocore.config import Config
    client = boto3.client('ssm', region_name=region, config=Config(
        connect_timeout=5, read_timeout=10, retries={'total_max_attempts': 1}))
    return client.get_parameter(Name=parameter, WithDecryption=True)['Parameter']['Value']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('setup', 'run'))
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--allowlist')
    parser.add_argument('--vault-helper')
    parser.add_argument('--keygen-helper')
    parser.add_argument('--whatsapp-helper')
    parser.add_argument('--ssm-token-ref')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--take-over-bot', action='store_true')
    parser.add_argument('--max-polls', type=int, default=1000)
    args = parser.parse_args(argv)
    wiring, runner = None, None
    try:
        root = Path(args.state_dir).absolute()
        if args.action == 'setup':
            if any(value is None for value in (args.allowlist, args.vault_helper, args.keygen_helper, args.whatsapp_helper)):
                raise ValueError('pilot_setup_arguments')
            print(json.dumps(setup(root=root, allowlist_path=args.allowlist,
                vault_helper=args.vault_helper, keygen_helper=args.keygen_helper,
                whatsapp_helper=args.whatsapp_helper)))
            return 0
        if not 1 <= args.max_polls <= 10000:
            raise ValueError('pilot_poll_limit')
        config_path = private_operator_path(root / 'config.json')
        allowlist_path = private_operator_path(root / 'allowlist.json')
        secret_path = private_operator_path(root / 'webhook-secret.txt')
        state_db = private_operator_path(root / 'control.sqlite', new_file=True)
        # HTTP timeout must exceed the 20 s getUpdates long poll.
        api = BotApi(_token(args.ssm_token_ref, args.region), timeout=35)
        wiring, runtime = from_config(state_db=state_db,
            phone_allowlist=PhoneAllowlist.from_file(allowlist_path), secret=secret_path.read_text(),
            config_path=config_path, api=api)
        config = json.loads(config_path.read_text())
        runner = PollingRunner(api, wiring, bot_ref='bot:pilot',
            lease_seconds=config.get('polling_lease_seconds', 300),
            diagnostic=lambda code: print(json.dumps({'component': 'telegram', 'code': code}), flush=True))
        runner.prepare(take_over_bot=args.take_over_bot)
        print(json.dumps({'pilot': 'running', 'llm_enabled': True}), flush=True)
        runner.run(max_polls=args.max_polls)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(json.dumps({'pilot': 'blocked', 'type': type(error).__name__}), flush=True)
        return 2
    finally:
        if runner is not None:
            runner.close()
        if wiring is not None:
            wiring.store.close()


if __name__ == '__main__':
    raise SystemExit(main())
