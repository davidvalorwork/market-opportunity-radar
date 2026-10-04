"""Real offline age helper; ephemeral SYNTHETIC keys, no external network."""
from hashlib import sha256
import os
from pathlib import Path
import subprocess

import pytest

from radar.adapters.local.private_vault import PrivateVault,create_private_directory
from radar.adapters.local.general_fixture import OWNER

ROOT=Path(__file__).resolve().parents[2]
SCOPES=('worker:task-router','worker:sources','worker:research','worker:conversations','worker:contact','worker:whatsapp')


@pytest.fixture(scope='session')
def general_age_helpers():
    output=ROOT/'.local'/'general-age-build'
    output.mkdir(parents=True,exist_ok=True)
    suffix='.exe' if os.name=='nt' else ''
    helper,keygen=output/('vault'+suffix),output/('synthetic-keys'+suffix)
    environment={**os.environ,'GOPROXY':'off','GOSUMDB':'off','GOTOOLCHAIN':'local','GOWORK':'off'}
    for target,package in ((helper,'./cmd/vault'),(keygen,'./cmd/synthetic-keys')):
        result=subprocess.run(['go','build','-mod=readonly','-trimpath','-buildvcs=false','-o',str(target),package],
            cwd=ROOT/'helpers'/'private-vault',env=environment,capture_output=True,timeout=60)
        assert result.returncode==0,'offline helper build failed'
    return helper,keygen


@pytest.fixture
def general_age_config(tmp_path,general_age_helpers):
    helper,keygen=general_age_helpers
    root=create_private_directory(tmp_path/'general-vault')
    keys=create_private_directory(tmp_path/'general-keys')
    result=subprocess.run([str(keygen),'--synthetic-only',str(keys)],capture_output=True,timeout=10)
    assert result.returncode==0 and not result.stdout and not result.stderr
    return {'root':root,'helper':helper,'helper_sha256':sha256(helper.read_bytes()).hexdigest(),
        'identity_file':keys/'identity.agekey','recipient':(keys/'recipient.txt').read_text().strip()}


def real_views(config,store):
    vault=PrivateVault(**config,store=store,owner_ref=OWNER,audiences=SCOPES,
        authorize=lambda owner_ref,audience:owner_ref==OWNER)
    return {scope:vault.view(scope) for scope in SCOPES}
