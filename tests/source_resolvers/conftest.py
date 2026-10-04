"""Offline Go builds and private ephemeral TEST ONLY resolver executables."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from radar.adapters.local.private_vault import create_private_directory
from radar.adapters.sources.generic.model import ReadOperation, ReadRequest, SourceSpec
from radar.adapters.sources.resolvers import PublicDNSResolver

ROOT=Path(__file__).resolve().parents[2]
HOST='fixture.invalid'
SPEC=SourceSpec('source:synthetic','web','stdlib','http',(ReadOperation('read','public_get'),),
                allowed_hosts=(HOST,'second.invalid'),min_interval_seconds=0,max_concurrency=4)


@pytest.fixture(scope='session')
def dns_helpers(tmp_path_factory):
    parent=tmp_path_factory.mktemp('a19-synthetic-binaries')
    directory=create_private_directory(parent/'private-build')
    env={**os.environ,'GOPROXY':'off','GOSUMDB':'off','GOTOOLCHAIN':'local','GOWORK':'off'}
    suffix='.exe' if os.name=='nt' else ''
    result={}
    for name in ('resolve','synthetic'):
        binary=directory/(name+suffix)
        built=subprocess.run(['go','build','-mod=readonly','-trimpath','-buildvcs=false','-o',str(binary),'./cmd/'+name],
                             cwd=ROOT/'helpers/public-dns',env=env,capture_output=True,timeout=60)
        assert built.returncode==0,'offline stdlib DNS helper build failed'
        if os.name!='nt':binary.chmod(0o700)
        result[name]=binary
    return result


@pytest.fixture
def resolver_factory(dns_helpers,tmp_path):
    counter=0
    def factory(mode='normal',**kwargs):
        nonlocal counter
        counter+=1
        private=create_private_directory(tmp_path/f'new-private-{counter}')
        binary=private/('synthetic.exe' if os.name=='nt' else 'synthetic')
        shutil.copyfile(dns_helpers['synthetic'],binary)
        if os.name!='nt':binary.chmod(0o700)
        binary.with_name(binary.name+'.synthetic-only').write_text(mode,encoding='ascii')
        return PublicDNSResolver(helper=binary,helper_sha256=sha256(binary.read_bytes()).hexdigest(),specs=(SPEC,),**kwargs)
    return factory


def request(*,owner='owner:synthetic',request_ref='request:synthetic',timeout=10,deadline=None):
    return ReadRequest(owner,'user:synthetic',request_ref,SPEC.source_ref,'read',
                       deadline or datetime.now(timezone.utc)+timedelta(seconds=timeout),
                       target_url='https://fixture.invalid/note')
