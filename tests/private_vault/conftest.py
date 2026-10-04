"""REAL offline crypto, SYNTHETIC ephemeral keys. No network/downloads."""
from hashlib import sha256
import os
from pathlib import Path
import subprocess

import pytest

from radar.adapters.local.private_vault import PrivateVault, create_private_directory

ROOT = Path(__file__).resolve().parents[2]
OWNER = 'owner:synthetic'
AUDIENCES = ('worker:task-router', 'worker:research', 'worker:sources', 'worker:polling')


@pytest.fixture(scope='session')
def binaries():
    output = ROOT / '.local' / 'private-vault-build'
    output.mkdir(parents=True, exist_ok=True)
    suffix = '.exe' if os.name == 'nt' else ''
    helper, keygen = output / ('vault' + suffix), output / ('synthetic-keys' + suffix)
    env = {**os.environ, 'GOPROXY': 'off', 'GOSUMDB': 'off', 'GOTOOLCHAIN': 'local', 'GOWORK': 'off'}
    for target, package in ((helper, './cmd/vault'), (keygen, './cmd/synthetic-keys')):
        result = subprocess.run(['go', 'build', '-mod=readonly', '-trimpath', '-buildvcs=false', '-o', str(target), package],
                                cwd=ROOT / 'helpers' / 'private-vault', env=env,
                                capture_output=True, timeout=60)
        assert result.returncode == 0, 'offline helper build failed'
    return helper, keygen


@pytest.fixture
def vault_factory(tmp_path, binaries):
    helper, keygen = binaries
    count = 0
    def factory(*, store=None, authorize=lambda **kw: True, owner=OWNER):
        nonlocal count
        count += 1
        root = create_private_directory(tmp_path / f'vault-{count}')
        keys = create_private_directory(tmp_path / f'keys-{count}')
        result = subprocess.run([str(keygen), '--synthetic-only', str(keys)], capture_output=True, timeout=10)
        assert result.returncode == 0 and result.stdout == b'' and result.stderr == b''
        config = dict(root=root, helper=helper, helper_sha256=sha256(helper.read_bytes()).hexdigest(),
                      identity_file=keys / 'identity.agekey', recipient=(keys / 'recipient.txt').read_text().strip(),
                      owner_ref=owner, audiences=AUDIENCES, authorize=authorize, store=store)
        return PrivateVault(**config), config
    return factory
