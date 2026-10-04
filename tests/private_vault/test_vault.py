from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
import struct

import pytest

from radar.adapters.local import private_vault as module
from radar.adapters.local.private_vault import (MAX_CONTENT, PrivateVault, VaultError,
                                                create_private_directory)
from radar.adapters.local.sqlite import SQLiteStore
from radar.ports.types import BlobPointer
from conftest import OWNER

SECRET = b'SYNTHETIC Elena +58-000-123-4567 private text'


def test_real_age_restart_cipher_digest_privacy(vault_factory, capsys, caplog):
    backend, config = vault_factory()
    view = backend.view('worker:research')
    pointer = view.seal(owner_ref=OWNER, plaintext=SECRET)
    ciphertext = (config['root'] / pointer.blob_key).read_bytes()
    assert ciphertext.startswith(b'age-encryption.org/v1\n')
    assert SECRET not in ciphertext and sha256(ciphertext).hexdigest() == pointer.sha256
    assert sha256(SECRET).hexdigest() != pointer.sha256
    assert PrivateVault(**config).view('worker:research').open(owner_ref=OWNER, pointer=pointer) == SECRET
    assert SECRET.decode() not in repr(backend) + repr(view) + repr(pointer) + caplog.text
    assert capsys.readouterr().out == ''
    assert len(list(config['root'].glob('*.age'))) == 1


@pytest.mark.parametrize('audience', ['worker:research', 'worker:task-router', 'worker:sources', 'worker:polling'])
def test_views_real_round_trip_and_preflight(vault_factory, audience):
    backend, _ = vault_factory()
    view = backend.view(audience)
    assert view.preflight(owner_ref=OWNER) is True
    pointer = view.seal(owner_ref=OWNER, plaintext=SECRET)
    assert view.open(owner_ref=OWNER, pointer=pointer) == SECRET
    assert view.get(owner_ref=OWNER, ref=view.put(owner_ref=OWNER, content=b'')) == b''


@pytest.mark.parametrize('change', ['owner', 'scope', 'hash', 'key', 'traversal'])
def test_cross_bindings(vault_factory, change):
    backend, _ = vault_factory()
    view = backend.view('worker:research')
    pointer = view.seal(owner_ref=OWNER, plaintext=SECRET)
    owner = OWNER
    if change == 'owner': owner = 'owner:foreign'
    elif change == 'scope': pointer = replace(pointer, recipient_scope='worker:sources')
    elif change == 'hash': pointer = replace(pointer, sha256='0' * 64)
    elif change == 'key': pointer = replace(pointer, blob_key='a' * 32 + '.age')
    else: pointer = replace(pointer, blob_key='../identity.agekey')
    with pytest.raises(VaultError):
        view.open(owner_ref=owner, pointer=pointer)


def test_swapped_valid_ciphertexts_and_get_reject_authenticated_key_binding(vault_factory):
    backend, config = vault_factory()
    view = backend.view('worker:research')
    one = view.seal(owner_ref=OWNER, plaintext=b'first')
    two = view.seal(owner_ref=OWNER, plaintext=b'second')
    (config['root'] / one.blob_key).write_bytes((config['root'] / two.blob_key).read_bytes())
    with pytest.raises(VaultError, match='binding_rejected'):
        view.open(owner_ref=OWNER, pointer=replace(one, sha256=two.sha256))
    with pytest.raises(VaultError, match='binding_rejected'):
        view.get(owner_ref=OWNER, ref=one.blob_key)


def test_audience_cannot_be_relabelled_even_with_valid_cipher_hash(vault_factory):
    backend, _ = vault_factory()
    pointer = backend.view('worker:sources').seal(owner_ref=OWNER, plaintext=SECRET)
    with pytest.raises(VaultError, match='binding_rejected'):
        backend.view('worker:research').open(owner_ref=OWNER, pointer=replace(pointer, recipient_scope='worker:research'))
    with pytest.raises(VaultError): backend.view('worker:unconfigured')
    with pytest.raises(VaultError): backend.view('worker:research').source_sink()


def test_foreign_owner_with_same_real_key_cannot_relabel(vault_factory):
    backend, config = vault_factory()
    pointer = backend.view('worker:research').seal(owner_ref=OWNER, plaintext=SECRET)
    foreign = PrivateVault(**{**config, 'owner_ref': 'owner:foreign'})
    with pytest.raises(VaultError, match='binding_rejected'):
        foreign.view('worker:research').open(owner_ref='owner:foreign', pointer=pointer)


def test_revoked_before_read_and_during_encryption(vault_factory, monkeypatch):
    allowed = [True]
    backend, config = vault_factory(authorize=lambda **kw: allowed[0])
    view = backend.view('worker:research')
    pointer = view.seal(owner_ref=OWNER, plaintext=SECRET)
    allowed[0] = False
    assert view.preflight(owner_ref=OWNER) is False
    with pytest.raises(VaultError, match='access_denied'): view.open(owner_ref=OWNER, pointer=pointer)
    allowed[0] = True
    original = module._invoke
    def revoke(*args):
        result = original(*args)
        allowed[0] = False
        return result
    monkeypatch.setattr(module, '_invoke', revoke)
    with pytest.raises(VaultError, match='access_denied'): view.seal(owner_ref=OWNER, plaintext=b'new')
    assert len(list(config['root'].glob('*.age'))) == 1


def test_size_limits_zero_content_and_wrong_identity(vault_factory):
    backend, config = vault_factory()
    view = backend.view('worker:research')
    pointer = view.seal(owner_ref=OWNER, plaintext=b'x' * MAX_CONTENT)
    assert len(view.open(owner_ref=OWNER, pointer=pointer)) == MAX_CONTENT
    with pytest.raises(VaultError, match='limit_rejected'): view.open(owner_ref=OWNER, pointer=pointer, max_bytes=MAX_CONTENT-1)
    with pytest.raises(VaultError, match='limit_rejected'): view.seal(owner_ref=OWNER, plaintext=b'x' * (MAX_CONTENT+1))
    other, other_config = vault_factory()
    with pytest.raises(VaultError, match='helper_failed'):
        PrivateVault(**{**config, 'identity_file': other_config['identity_file']})


def test_helper_digest_changed_rejected(vault_factory):
    _, config = vault_factory()
    with pytest.raises(VaultError, match='integrity_rejected'):
        PrivateVault(**{**config, 'helper_sha256': '0' * 64})


def test_create_existing_directory_never_changes_permissions(vault_factory):
    _, config = vault_factory()
    before = config['root'].stat()
    with pytest.raises(VaultError): create_private_directory(config['root'])
    assert config['root'].stat().st_mode == before.st_mode


def test_acl_actual_permissions_and_untrusted_allow_rejected(vault_factory, tmp_path):
    backend, config = vault_factory()
    assert backend.view('worker:research').preflight(owner_ref=OWNER)
    if os.name == 'nt':
        acl = module._WindowsACL()
        acl.check(config['root']); acl.check(config['identity_file'])
        # Real WinAPI ACL mutation ONLY on this test's ephemeral new vault.
        descriptor, dacl = module.ctypes.c_void_p(), module.ctypes.c_void_p()
        assert acl.adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f'D:P(A;OICI;FA;;;{acl.sid})(A;OICI;FR;;;WD)', 1, module.ctypes.byref(descriptor), None)
        try:
            present, defaulted = acl.w.BOOL(), acl.w.BOOL()
            assert acl.adv.GetSecurityDescriptorDacl(descriptor, module.ctypes.byref(present), module.ctypes.byref(dacl), module.ctypes.byref(defaulted))
            assert acl.adv.SetNamedSecurityInfoW(str(config['root']), 1, 0x80000004, None, None, dacl, None) == 0
            with pytest.raises(VaultError, match='permissions_rejected'): acl.check(config['root'])
        finally: acl.kernel.LocalFree(descriptor)
    else:
        assert config['root'].stat().st_mode & 0o777 == 0o700
        assert config['identity_file'].stat().st_mode & 0o777 == 0o600
        config['root'].chmod(0o755)
    assert backend.view('worker:research').preflight(owner_ref=OWNER) is False


def test_hardlink_rejected_and_publish_never_overwrites(vault_factory, tmp_path):
    backend, config = vault_factory()
    view = backend.view('worker:research')
    pointer = view.seal(owner_ref=OWNER, plaintext=SECRET)
    ciphertext = (config['root'] / pointer.blob_key).read_bytes()
    with pytest.raises((VaultError, OSError)): backend._publish(pointer.blob_key, b'different')
    assert (config['root'] / pointer.blob_key).read_bytes() == ciphertext
    os.link(config['root'] / pointer.blob_key, tmp_path / 'extra-link')
    with pytest.raises(VaultError, match='path_rejected'): view.open(owner_ref=OWNER, pointer=pointer)


def test_fsync_failure_returns_no_pointer_and_cleans_temp(vault_factory, monkeypatch):
    backend, config = vault_factory()
    monkeypatch.setattr(module.os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('SYNTHETIC_SECRET')))
    with pytest.raises(VaultError, match='storage_failed') as error:
        backend.view('worker:research').seal(owner_ref=OWNER, plaintext=SECRET)
    assert SECRET.decode() not in repr(error.value) and not list(config['root'].glob('*.age'))


def test_control_only_ciphertext_and_scope_immutable(vault_factory, tmp_path):
    store = SQLiteStore(tmp_path / 'control.sqlite')
    store.enroll(owner_ref=OWNER, actor_ref='user:synthetic')
    backend, _ = vault_factory(store=store)
    pointer = backend.view('worker:task-router').seal(owner_ref=OWNER, plaintext=SECRET)
    cipher = store.db.execute('SELECT content FROM blobs').fetchone()[0]
    assert cipher.startswith(b'age-encryption.org/v1\n') and SECRET not in cipher
    backend._register(OWNER, pointer, cipher)
    with pytest.raises(VaultError, match='storage_failed'):
        backend._register(OWNER, replace(pointer, recipient_scope='worker:research'), cipher)
    assert store.db.execute('SELECT scope FROM private_vault_refs').fetchone()[0] == 'worker:task-router'
    store.close()
    assert SECRET not in (tmp_path / 'control.sqlite').read_bytes()


@pytest.mark.parametrize('behavior,code', [('timeout','helper_timeout'), ('stdout','helper_failed'), ('stderr','helper_failed'), ('exit','helper_failed')])
def test_hostile_pipe_limits_kill_await_static_errors(vault_factory, monkeypatch, behavior, code):
    backend, _ = vault_factory()
    processes, original = [], subprocess.Popen
    def spawn(argv, **kwargs):
        assert SECRET.decode() not in repr(argv) and argv[-1] == 'encrypt'
        child = original([sys.executable, str(Path(__file__).with_name('pipe_fixture.py')), 'encrypt'], **kwargs)
        processes.append(child)
        return child
    monkeypatch.setattr(module.subprocess, 'Popen', spawn)
    start = monotonic()
    with pytest.raises(VaultError, match=code) as error:
        module._invoke(backend._helper, 'encrypt', b'fixture:key', ('fixture:' + behavior).encode(), 0.15)
    assert monotonic()-start < 4 and all(p.poll() is not None for p in processes)
    assert 'SYNTHETIC_SECRET' not in str(error.value) and not error.value.__cause__


def test_malformed_encrypt_output_rejected_before_disk(vault_factory, monkeypatch):
    backend, config = vault_factory()
    monkeypatch.setattr(module, '_invoke', lambda *args: b'malformed SYNTHETIC_SECRET')
    with pytest.raises(VaultError, match='helper_failed'):
        backend.view('worker:research').seal(owner_ref=OWNER, plaintext=SECRET)
    assert not list(config['root'].iterdir())


def test_total_quota_threads_restart_orphans_and_owner_binding(vault_factory):
    backend, config = vault_factory()
    config.update(max_blobs=2, max_total_bytes=10000)
    backend = PrivateVault(**config)
    view = backend.view('worker:research')
    def write(_):
        try:
            return view.seal(owner_ref=OWNER, plaintext=SECRET)
        except VaultError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(write, range(6)))
    assert sum(isinstance(item, BlobPointer) for item in outcomes) == 2
    assert outcomes.count('quota_exhausted') == 4
    assert len(list(config['root'].glob('*.age'))) == 2
    with pytest.raises(VaultError, match='quota_exhausted'):
        PrivateVault(**config).view('worker:research').seal(owner_ref=OWNER, plaintext=b'new')
    foreign = PrivateVault(**{**config, 'owner_ref': 'owner:foreign'})
    with pytest.raises(VaultError, match='binding_rejected'):
        foreign.view('worker:research').seal(owner_ref='owner:foreign', plaintext=b'new')


def test_total_byte_quota_counts_unindexed_crash_orphan(vault_factory, monkeypatch):
    backend, config = vault_factory()
    config.update(max_total_bytes=1024)
    backend = PrivateVault(**config)
    monkeypatch.setattr(backend, '_register', lambda *args: (_ for _ in ()).throw(OSError('crash fixture')))
    with pytest.raises(VaultError, match='storage_failed'):
        backend.view('worker:research').seal(owner_ref=OWNER, plaintext=b'x' * 500)
    files = list(config['root'].glob('*.age'))
    assert len(files) == 1 and files[0].stat().st_size > 500
    with pytest.raises(VaultError, match='quota_exhausted'):
        PrivateVault(**config).view('worker:research').seal(owner_ref=OWNER, plaintext=b'new')
    assert files[0].exists()  # No inferred safe deletion/GC.


def test_cross_process_quota_offline(vault_factory):
    _, config = vault_factory()
    config.update(max_blobs=2, max_total_bytes=10000)
    body = json.dumps({k: str(v) if isinstance(v, Path) else v
                       for k,v in config.items() if k not in ('authorize','store')}).encode()
    def launch(_):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('child_write.py'))],
                                input=body, capture_output=True, timeout=15)
        assert result.returncode == 0 and result.stderr == b''
        return result.stdout.strip()
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(launch, range(4)))
    assert outcomes.count(b'ok') == 2 and outcomes.count(b'quota_exhausted') == 2


def test_changed_identity_permissions_and_content_rejected(vault_factory):
    backend, config = vault_factory()
    config['identity_file'].write_bytes(b'AGE-SECRET-KEY-SYNTHETIC-INVALID')
    assert backend.view('worker:research').preflight(owner_ref=OWNER) is False
    with pytest.raises(VaultError, match='integrity_rejected'):
        backend.view('worker:research').seal(owner_ref=OWNER, plaintext=SECRET)


def test_symlink_rejected_platform_specific_without_skips(vault_factory, monkeypatch):
    backend, config = vault_factory()
    pointer = backend.view('worker:research').seal(owner_ref=OWNER, plaintext=SECRET)
    target = config['root'] / pointer.blob_key
    if os.name != 'nt':
        target.unlink()
        target.symlink_to(config['identity_file'])
    else:
        # Windows symlink creation needs privileges; test rejection of the actual
        # reparse-attribute branch without claiming a privileged symlink was made.
        original = Path.lstat
        class Reparse:
            st_mode = 0o100600
            st_file_attributes = 0x400
        monkeypatch.setattr(Path, 'lstat', lambda self: Reparse() if self == target else original(self))
    with pytest.raises(VaultError, match='path_rejected'):
        backend.view('worker:research').open(owner_ref=OWNER, pointer=pointer)


@pytest.mark.parametrize('change', ['owner', 'audience', 'key', 'hash', 'size', 'version', 'malformed'])
def test_real_cipher_authenticated_envelope_binding(vault_factory, change):
    backend, config = vault_factory()
    name = 'f' * 32 + '.age'
    content = b'SYNTHETIC content'
    header = {'v':1, 'owner':OWNER, 'audience':'worker:research', 'key':name,
              'hash':sha256(content).hexdigest(), 'size':len(content)}
    if change == 'owner': header['owner'] = 'owner:foreign'
    elif change == 'audience': header['audience'] = 'worker:sources'
    elif change == 'key': header['key'] = 'e' * 32 + '.age'
    elif change == 'hash': header['hash'] = '0' * 64
    elif change == 'size': header['size'] += 1
    elif change == 'version': header['v'] = 2
    raw = b'invalid' if change == 'malformed' else json.dumps(header).encode()
    cipher = module._invoke(backend._helper,'encrypt',backend._recipient,struct.pack('>I',len(raw))+raw+content,5)
    backend._publish(name,cipher)
    pointer = BlobPointer(name,sha256(cipher).hexdigest(),'worker:research')
    with pytest.raises(VaultError, match='binding_rejected'):
        backend.view('worker:research').open(owner_ref=OWNER,pointer=pointer)


def test_real_age_tampered_authentication_even_if_transport_hash_updated(vault_factory):
    backend, config = vault_factory()
    view = backend.view('worker:research')
    pointer = view.seal(owner_ref=OWNER,plaintext=SECRET)
    path = config['root'] / pointer.blob_key
    cipher = bytearray(path.read_bytes()); cipher[-1] ^= 1
    path.write_bytes(cipher)
    with pytest.raises(VaultError, match='helper_failed'):
        view.open(owner_ref=OWNER,pointer=replace(pointer,sha256=sha256(cipher).hexdigest()))


def test_sqlite_control_tamper_and_owner_stop_rejected(vault_factory,tmp_path):
    store = SQLiteStore(tmp_path/'control.sqlite')
    store.enroll(owner_ref=OWNER,actor_ref='user:synthetic')
    backend,_ = vault_factory(store=store)
    view = backend.view('worker:research')
    pointer = view.seal(owner_ref=OWNER,plaintext=SECRET)
    store.db.execute('UPDATE private_vault_refs SET scope=?',('worker:sources',))
    with pytest.raises(VaultError,match='integrity_rejected'): view.open(owner_ref=OWNER,pointer=pointer)
    store.stop(owner_ref=OWNER)
    assert view.preflight(owner_ref=OWNER) is False
    with pytest.raises(VaultError,match='access_denied'): view.seal(owner_ref=OWNER,plaintext=SECRET)
    store.close()


def test_owner_quota_cannot_change_between_processes(vault_factory):
    backend,config = vault_factory()
    backend.view('worker:research').seal(owner_ref=OWNER,plaintext=SECRET)
    for changed in ({'max_blobs':9999},{'max_total_bytes':128*module.MAX_PLAIN}):
        other = PrivateVault(**{**config,**changed})
        with pytest.raises(VaultError,match='configuration_rejected'):
            other.view('worker:research').seal(owner_ref=OWNER,plaintext=SECRET)
