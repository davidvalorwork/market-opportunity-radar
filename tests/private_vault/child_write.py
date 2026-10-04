"""Explicit synthetic cross-process quota fixture; config uses stdin only."""
import json
import sys
from radar.adapters.local.private_vault import PrivateVault, VaultError

config = json.loads(sys.stdin.buffer.read(16384))
config['audiences'] = tuple(config['audiences'])
config['authorize'] = lambda **kw: True
try:
    vault = PrivateVault(**config)
    vault.view('worker:research').seal(owner_ref=config['owner_ref'], plaintext=b'SYNTHETIC subprocess')
    print('ok')
except VaultError as error:
    print(error.code)
