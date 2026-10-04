"""Local real-age vault. Explicit owner/audience views; no implicit bot wiring.

Content and keys travel through private pipes, never shell arguments. Authorization
is a trusted deployment callback, NOT an assertion supplied in a task document.
"""
from contextlib import contextmanager, nullcontext
from hashlib import sha256
import ctypes
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
from threading import Event, Thread
from time import monotonic
from uuid import uuid4

from radar.ports.types import BlobPointer

MAX_PLAIN = 1 << 20
MAX_CIPHER = MAX_PLAIN + 4096
MAX_CONTENT = MAX_PLAIN - 1024
OWNER = re.compile(r'[a-z][a-z0-9_]{0,31}:[A-Za-z0-9_-]*[A-Za-z][A-Za-z0-9_-]*')
SCOPE = re.compile(r'[a-z][a-z0-9_]{0,31}:[a-z][a-z0-9_-]{0,63}')
KEY = re.compile(r'[0-9a-f]{32}\.age')
HASH = re.compile(r'[0-9a-f]{64}')
CODES = frozenset({'configuration_rejected', 'permissions_rejected', 'path_rejected',
                   'access_denied', 'binding_rejected', 'limit_rejected', 'helper_failed',
                   'helper_timeout', 'storage_failed', 'integrity_rejected', 'quota_exhausted'})


class VaultError(Exception):
    def __init__(self, code):
        self.code = code if code in CODES else 'storage_failed'
        super().__init__(self.code)


def _reject(code):
    raise VaultError(code) from None


def _absolute(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        _reject('path_rejected')
    for component in (path, *path.parents):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            _reject('path_rejected')
    return path


class _WindowsACL:
    """SID-based WinAPI; no localized account names or subprocess ACL parsing."""
    def __init__(self):
        from ctypes import wintypes as w
        self.w, self.adv = w, ctypes.WinDLL('advapi32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        P = ctypes.c_void_p
        self.kernel.GetCurrentProcess.restype = w.HANDLE
        self.kernel.LocalFree.argtypes = [P]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
        self.adv.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, P, w.DWORD, ctypes.POINTER(w.DWORD)]
        self.adv.ConvertSidToStringSidW.argtypes = [P, ctypes.POINTER(P)]
        self.adv.GetNamedSecurityInfoW.argtypes = [w.LPWSTR, ctypes.c_int, w.DWORD, ctypes.POINTER(P), P, ctypes.POINTER(P), P, ctypes.POINTER(P)]
        self.adv.GetAclInformation.argtypes = [P, P, w.DWORD, ctypes.c_int]
        self.adv.GetAce.argtypes = [P, w.DWORD, ctypes.POINTER(P)]
        self.adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.POINTER(P), P]
        self.adv.GetSecurityDescriptorDacl.argtypes = [P, ctypes.POINTER(w.BOOL), ctypes.POINTER(P), ctypes.POINTER(w.BOOL)]
        self.adv.SetNamedSecurityInfoW.argtypes = [w.LPWSTR, ctypes.c_int, w.DWORD, P, P, P, P]
        token = w.HANDLE()
        if not self.adv.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            _reject('permissions_rejected')
        try:
            size = w.DWORD()
            self.adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            if not self.adv.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
                _reject('permissions_rejected')
            self.sid = self._sid(ctypes.cast(buffer, ctypes.POINTER(P))[0])
        finally:
            self.kernel.CloseHandle(token)

    def _sid(self, pointer):
        value = ctypes.c_void_p()
        if not self.adv.ConvertSidToStringSidW(pointer, ctypes.byref(value)):
            _reject('permissions_rejected')
        try:
            return ctypes.wstring_at(value)
        finally:
            self.kernel.LocalFree(value)

    def check(self, path):
        owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        if self.adv.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor)):
            _reject('permissions_rejected')
        try:
            if not owner.value or self._sid(owner) != self.sid or not dacl.value:
                _reject('permissions_rejected')
            info = (self.w.DWORD * 3)()
            if not self.adv.GetAclInformation(dacl, info, ctypes.sizeof(info), 2) or not info[0]:
                _reject('permissions_rejected')
            own_allow = False
            for index in range(info[0]):
                ace = ctypes.c_void_p()
                if not self.adv.GetAce(dacl, index, ctypes.byref(ace)):
                    _reject('permissions_rejected')
                header = ctypes.string_at(ace, 4)
                kind, flags, length = struct.unpack('<BBH', header)
                # Only basic Allow/Deny ACEs are supported. Unknown/object/callback
                # ACEs fail closed instead of approximating effective access.
                if kind not in (0, 1) or length < 12:
                    _reject('permissions_rejected')
                if kind == 0:
                    sid = self._sid(ace.value + 8)
                    if sid not in (self.sid, 'S-1-5-18'):
                        _reject('permissions_rejected')
                    own_allow |= sid == self.sid and not (flags & 8)
            if not own_allow:
                _reject('permissions_rejected')
        finally:
            self.kernel.LocalFree(descriptor)

    def restrict(self, path):
        descriptor, dacl = ctypes.c_void_p(), ctypes.c_void_p()
        sddl = f'D:P(A;OICI;FA;;;{self.sid})(A;OICI;FA;;;SY)'
        if not self.adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            _reject('permissions_rejected')
        try:
            present, defaulted = self.w.BOOL(), self.w.BOOL()
            if not self.adv.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)):
                _reject('permissions_rejected')
            if self.adv.SetNamedSecurityInfoW(str(path), 1, 0x80000004, None, None, dacl, None):
                _reject('permissions_rejected')
            self.check(path)
        finally:
            self.kernel.LocalFree(descriptor)


def _permissions(path, *, directory=False):
    path = _absolute(path)
    info = path.stat()
    if directory != stat.S_ISDIR(info.st_mode) or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
        _reject('path_rejected')
    if os.name == 'nt':
        _WindowsACL().check(path)
    elif info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        _reject('permissions_rejected')
    return info


def create_private_directory(path):
    """Explicit setup of ONE new leaf only. Never alters an existing directory."""
    try:
        path = Path(path)
        if not path.is_absolute() or '..' in path.parts:
            _reject('path_rejected')
        _absolute(path.parent)
        path.mkdir(mode=0o700, exist_ok=False)
        if os.name == 'nt':
            _WindowsACL().restrict(path)
        _permissions(path, directory=True)
        return path
    except VaultError:
        raise
    except OSError:
        _reject('storage_failed')


def _invoke(executable, mode, key, body, timeout):
    """Bounded concurrent pipe drains; timeout/overflow kill and await our child."""
    if mode not in ('encrypt', 'decrypt') or not 0 < len(key) <= 4096:
        _reject('configuration_rejected')
    limit = MAX_CIPHER if mode == 'encrypt' else MAX_PLAIN
    payload = struct.pack('>I', len(key)) + key + body
    process, threads = None, []
    overflow, output = Event(), []
    try:
        process = subprocess.Popen([str(executable), mode], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   shell=False, env={}, close_fds=True)
        def drain(pipe, maximum, retain):
            chunks, count = [], 0
            try:
                while chunk := pipe.read(4096):
                    count += len(chunk)
                    if count > maximum:
                        overflow.set()
                        break
                    if retain:
                        chunks.append(chunk)
                if retain:
                    output.append(b''.join(chunks))
            except (OSError, ValueError):
                overflow.set()
            finally:
                pipe.close()
        def write():
            try:
                process.stdin.write(payload)
                process.stdin.close()
            except (OSError, ValueError):
                pass
        threads = [Thread(target=drain, args=(process.stdout, limit, True), daemon=True),
                   Thread(target=drain, args=(process.stderr, 256, False), daemon=True),
                   Thread(target=write, daemon=True)]
        for thread in threads:
            thread.start()
        deadline = monotonic() + timeout
        while process.poll() is None:
            if overflow.wait(min(0.01, max(0, deadline-monotonic()))):
                _reject('helper_failed')
            if monotonic() >= deadline:
                _reject('helper_timeout')
        for thread in threads:
            thread.join(max(0, deadline-monotonic()))
        if process.returncode or overflow.is_set() or any(t.is_alive() for t in threads) or len(output) != 1 or not output[0]:
            _reject('helper_failed')
        return output[0]
    except VaultError:
        raise
    except (OSError, ValueError):
        _reject('helper_failed')
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
            for thread in threads:
                thread.join(2)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe and not pipe.closed:
                    pipe.close()


class PrivateVault:
    """Fixed-owner backend with explicitly allowlisted audience views.

authorize(owner_ref=..., audience=...) must return exactly True each operation.
store is optional A3 SQLiteStore; it receives ONLY ciphertext plus control refs.
"""
    def __init__(self, *, root, helper, helper_sha256, identity_file, recipient,
                 owner_ref, audiences, authorize, store=None, timeout=5,
                 max_blobs=1024, max_total_bytes=64*MAX_PLAIN):
        try:
            if (not isinstance(owner_ref, str) or len(owner_ref) > 129 or not OWNER.fullmatch(owner_ref)
                or not isinstance(recipient, str) or not recipient.startswith('age1') or len(recipient) > 128
                or not isinstance(helper_sha256, str) or not HASH.fullmatch(helper_sha256)
                or not isinstance(audiences, tuple) or not audiences
                or any(not isinstance(a, str) or not SCOPE.fullmatch(a) for a in audiences)
                or not callable(authorize) or type(timeout) not in (int, float) or not 0 < timeout <= 10
                or type(max_blobs) is not int or not 1 <= max_blobs <= 10000
                or type(max_total_bytes) is not int or not 1024 <= max_total_bytes <= 1024*MAX_PLAIN):
                _reject('configuration_rejected')
            self._root, self._helper, self._identity = map(_absolute, (root, helper, identity_file))
            self._helper_hash, self._recipient = helper_sha256, recipient.encode('ascii')
            self._owner, self._audiences = owner_ref, frozenset(audiences)
            self._authorize, self._store, self._timeout = authorize, store, timeout
            self._max_blobs, self._max_bytes = max_blobs, max_total_bytes
            info = _permissions(self._root, directory=True)
            self._root_id = (info.st_dev, info.st_ino)
            self._key_id = None
            self._key_hash = None
            self._check_files()
            probe = b'private-vault-key-binding-v1'
            encrypted = _invoke(self._helper, 'encrypt', self._recipient, probe, self._timeout)
            if _invoke(self._helper, 'decrypt', self._read_identity(), encrypted, self._timeout) != probe:
                _reject('integrity_rejected')
            if store is not None:
                store.db.execute('CREATE TABLE IF NOT EXISTS private_vault_refs(owner TEXT,key TEXT,scope TEXT,hash TEXT,PRIMARY KEY(owner,key))')
        except VaultError:
            raise
        except (OSError, ValueError, TypeError, UnicodeError):
            _reject('configuration_rejected')

    def __repr__(self):
        return 'PrivateVault(<private>)'

    def _check_files(self):
        root = _permissions(self._root, directory=True)
        if (root.st_dev, root.st_ino) != self._root_id:
            _reject('path_rejected')
        _permissions(self._identity.parent, directory=True)
        key = _permissions(self._identity)
        if self._key_id is not None and (key.st_dev, key.st_ino) != self._key_id:
            _reject('path_rejected')
        self._key_id = (key.st_dev, key.st_ino)
        if not 0 < key.st_size <= 4096:
            _reject('limit_rejected')
        identity = self._read_identity()
        fingerprint = sha256(identity).hexdigest()
        if self._key_hash is not None and fingerprint != self._key_hash:
            _reject('integrity_rejected')
        self._key_hash = fingerprint
        helper = _absolute(self._helper)
        if not helper.is_file() or helper.stat().st_size > 32 * MAX_PLAIN:
            _reject('configuration_rejected')
        with helper.open('rb') as stream:
            code = stream.read(32*MAX_PLAIN + 1)
        if len(code) > 32*MAX_PLAIN or sha256(code).hexdigest() != self._helper_hash:
            _reject('integrity_rejected')

    def _read_identity(self):
        fd = os.open(self._identity, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (info.st_dev, info.st_ino) != self._key_id or info.st_nlink != 1:
                _reject('path_rejected')
            identity = stream.read(4097)
        if not 0 < len(identity) <= 4096:
            _reject('limit_rejected')
        return identity

    def _gate(self, owner, audience):
        if owner != self._owner or audience not in self._audiences:
            _reject('access_denied')
        try:
            if self._authorize(owner_ref=owner, audience=audience) is not True:
                _reject('access_denied')
            if self._store is not None:
                self._store._active(owner)
            self._check_files()
        except VaultError:
            raise
        except Exception:
            _reject('access_denied')

    def view(self, audience):
        if audience not in self._audiences:
            _reject('access_denied')
        return VaultView(self, audience)

    @contextmanager
    def _directory(self):
        fd = None
        try:
            if os.name != 'nt':
                fd = os.open(self._root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) != self._root_id:
                    _reject('path_rejected')
            yield fd
        finally:
            if fd is not None:
                os.close(fd)

    def _path(self, name):
        if not isinstance(name, str) or not KEY.fullmatch(name):
            _reject('path_rejected')
        return self._root / name

    @contextmanager
    def _quota_lock(self):
        """One private root per owner; OS advisory lock works across restarts/processes."""
        with self._directory() as directory:
            name = '.vault.lock'
            path = self._root / name
            fd = os.open(path if directory is None else name,
                         os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600, dir_fd=directory)
            locked, deadline = False, monotonic() + self._timeout
            try:
                _permissions(path)
                while not locked:
                    try:
                        if os.name == 'nt':
                            import msvcrt
                            os.lseek(fd, 0, os.SEEK_SET)
                            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                    except OSError:
                        if monotonic() >= deadline:
                            _reject('storage_failed')
                        Event().wait(0.01)
                os.lseek(fd, 0, os.SEEK_SET)
                binding = os.read(fd, 1025)
                expected = json.dumps({'v': 1, 'owner_hash': sha256(self._owner.encode('ascii')).hexdigest(),
                                       'recipient_hash': sha256(self._recipient).hexdigest(),
                                       'max_blobs': self._max_blobs, 'max_total_bytes': self._max_bytes},
                                      sort_keys=True, separators=(',', ':')).encode('ascii')
                if binding:
                    try:
                        metadata = json.loads(binding)
                        if metadata['owner_hash'] != sha256(self._owner.encode('ascii')).hexdigest():
                            _reject('binding_rejected')
                    except (ValueError, TypeError, KeyError):
                        _reject('binding_rejected')
                    if binding != expected:
                        _reject('configuration_rejected')
                if not binding:
                    os.lseek(fd, 0, os.SEEK_SET)
                    if os.write(fd, expected) != len(expected):
                        _reject('storage_failed')
                    os.fsync(fd)
                yield directory
            finally:
                if locked:
                    if os.name == 'nt':
                        import msvcrt
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _quota(self, directory, extra_bytes):
        if extra_bytes > self._max_bytes:
            _reject('quota_exhausted')
        count, total = 0, extra_bytes
        for name in os.listdir(self._root if directory is None else directory):
            if name == '.vault.lock':
                continue
            self._path(name)
            info = os.stat(self._root / name if directory is None else name,
                           dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                _reject('path_rejected')
            count += 1
            total += info.st_size
            if count >= self._max_blobs or total > self._max_bytes:
                _reject('quota_exhausted')

    def _read(self, name, maximum):
        path = self._path(name)
        _permissions(path)
        with self._directory() as directory:
            fd = os.open(path if directory is None else name, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0), dir_fd=directory)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
                    _reject('limit_rejected')
                value = stream.read(maximum + 1)
                if not value or len(value) > maximum:
                    _reject('limit_rejected')
                return value

    def _publish(self, name, ciphertext):
        temp = uuid4().hex + '.age'
        with self._directory() as directory:
            temporary = self._root / temp
            try:
                fd = os.open(temporary if directory is None else temp,
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0),
                             0o600, dir_fd=directory)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(ciphertext)
                    stream.flush()
                    os.fsync(stream.fileno())
                _permissions(temporary)
                if os.name == 'nt':
                    # Same-directory, no REPLACE_EXISTING; write-through publication.
                    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                    kernel.MoveFileExW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
                    if not kernel.MoveFileExW(str(temporary), str(self._path(name)), 8):
                        _reject('storage_failed')
                else:
                    os.link(temp, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                    os.unlink(temp, dir_fd=directory)
                    os.fsync(directory)
            finally:
                try:
                    if directory is None:
                        temporary.unlink(missing_ok=True)
                    else:
                        os.unlink(temp, dir_fd=directory)
                except FileNotFoundError:
                    pass

    def _register(self, owner, pointer, ciphertext):
        if self._store is None:
            return
        db = self._store.db
        with db.lock:
            db.execute('SAVEPOINT private_vault_write')
            try:
                db.execute('INSERT OR IGNORE INTO blobs VALUES(?,?,?,?)', (owner, pointer.blob_key, pointer.sha256, ciphertext))
                db.execute('INSERT OR IGNORE INTO private_vault_refs VALUES(?,?,?,?)', (owner, pointer.blob_key, pointer.recipient_scope, pointer.sha256))
                self._registered(owner, pointer, ciphertext)
                db.execute('RELEASE private_vault_write')
            except Exception:
                db.execute('ROLLBACK TO private_vault_write')
                db.execute('RELEASE private_vault_write')
                _reject('storage_failed')

    def _registered(self, owner, pointer, ciphertext):
        if self._store is None:
            return
        row = self._store.db.execute('SELECT hash,content FROM blobs WHERE owner=? AND key=?', (owner, pointer.blob_key)).fetchone()
        scope = self._store.db.execute('SELECT scope,hash FROM private_vault_refs WHERE owner=? AND key=?', (owner, pointer.blob_key)).fetchone()
        if row != (pointer.sha256, ciphertext) or scope != (pointer.recipient_scope, pointer.sha256):
            _reject('integrity_rejected')

    def _seal(self, owner, audience, content):
        self._gate(owner, audience)
        if not isinstance(content, bytes) or len(content) > MAX_CONTENT:
            _reject('limit_rejected')
        name = uuid4().hex + '.age'
        header = json.dumps({'v': 1, 'owner': owner, 'audience': audience, 'key': name,
                             'hash': sha256(content).hexdigest(), 'size': len(content)},
                            sort_keys=True, separators=(',', ':')).encode('ascii')
        plaintext = struct.pack('>I', len(header)) + header + content
        ciphertext = _invoke(self._helper, 'encrypt', self._recipient, plaintext, self._timeout)
        if not ciphertext.startswith(b'age-encryption.org/v1\n'):
            _reject('helper_failed')
        pointer = BlobPointer(name, sha256(ciphertext).hexdigest(), audience)
        self._gate(owner, audience)
        # Consistent DB -> filesystem lock order also works inside A6's existing
        # transaction. No connection-private attributes or nested BEGIN.
        with self._store.db.lock if self._store is not None else nullcontext():
            with self._quota_lock() as directory:
                self._quota(directory, len(ciphertext))
                self._gate(owner, audience)
                self._publish(name, ciphertext)
                self._register(owner, pointer, ciphertext)
        return pointer

    def _open(self, owner, audience, pointer, maximum):
        self._gate(owner, audience)
        if not isinstance(pointer, BlobPointer) or pointer.recipient_scope != audience:
            _reject('binding_rejected')
        if not isinstance(pointer.sha256, str) or not HASH.fullmatch(pointer.sha256):
            _reject('integrity_rejected')
        if type(maximum) is not int or not 0 <= maximum <= MAX_CONTENT:
            _reject('limit_rejected')
        ciphertext = self._read(pointer.blob_key, MAX_CIPHER)
        if sha256(ciphertext).hexdigest() != pointer.sha256:
            _reject('integrity_rejected')
        self._registered(owner, pointer, ciphertext)
        identity = self._read_identity()
        if not 0 < len(identity) <= 4096:
            _reject('limit_rejected')
        plaintext = _invoke(self._helper, 'decrypt', identity, ciphertext, self._timeout)
        try:
            size, = struct.unpack('>I', plaintext[:4])
            if not 0 < size <= 1020 or len(plaintext) < size + 4:
                _reject('binding_rejected')
            header = json.loads(plaintext[4:4+size])
            content = plaintext[4+size:]
            expected = {'v': 1, 'owner': owner, 'audience': audience, 'key': pointer.blob_key,
                        'hash': sha256(content).hexdigest(), 'size': len(content)}
            if (header != expected or type(header.get('v')) is not int
                or type(header.get('size')) is not int):
                _reject('binding_rejected')
            if len(content) > maximum:
                _reject('limit_rejected')
            self._gate(owner, audience)
            return content
        except (ValueError, TypeError, struct.error, RecursionError):
            _reject('binding_rejected')


class VaultView:
    """Pinned audience, no method to relabel an existing ciphertext."""
    def __init__(self, backend, audience):
        self._backend, self._audience = backend, audience

    def __repr__(self):
        return 'VaultView(<private>)'

    def preflight(self, *, owner_ref):
        try:
            self._backend._gate(owner_ref, self._audience)
            # Real round trip also rejects identity/recipient mismatches.
            marker = b'private-vault-preflight-v1'
            pointer = self.seal(owner_ref=owner_ref, plaintext=marker)
            return self.open(owner_ref=owner_ref, pointer=pointer) == marker
        except Exception:
            return False

    def seal(self, *, owner_ref, plaintext):
        try:
            return self._backend._seal(owner_ref, self._audience, plaintext)
        except VaultError:
            raise
        except Exception:
            _reject('storage_failed')

    def open(self, *, owner_ref, pointer, max_bytes=MAX_CONTENT):
        try:
            return self._backend._open(owner_ref, self._audience, pointer, max_bytes)
        except VaultError:
            raise
        except Exception:
            _reject('storage_failed')

    def put(self, *, owner_ref, content):
        return self.seal(owner_ref=owner_ref, plaintext=content).blob_key

    def get(self, *, owner_ref, ref):
        try:
            self._backend._gate(owner_ref, self._audience)
            ciphertext = self._backend._read(ref, MAX_CIPHER)
            pointer = BlobPointer(ref, sha256(ciphertext).hexdigest(), self._audience)
            return self.open(owner_ref=owner_ref, pointer=pointer)
        except VaultError:
            raise
        except Exception:
            _reject('storage_failed')

    def read(self, *, owner_ref, pointer, max_bytes=MAX_CONTENT):
        return self.open(owner_ref=owner_ref, pointer=pointer, max_bytes=max_bytes)

    def source_sink(self):
        if self._audience != 'worker:sources':
            _reject('binding_rejected')
        return SourceSinkView(self)


class SourceSinkView:
    """A8 adapter DTO conversion stays in adapters, not application."""
    def __init__(self, view):
        self._view = view

    def __repr__(self):
        return 'SourceSinkView(<private>)'

    def put(self, *, owner_ref, content):
        from radar.adapters.sources.generic.model import PrivateWrite
        return PrivateWrite(owner_ref, self._view.seal(owner_ref=owner_ref, plaintext=content))

    def read(self, *, owner_ref, pointer, max_bytes=MAX_CONTENT):
        return self._view.open(owner_ref=owner_ref, pointer=pointer, max_bytes=min(max_bytes, MAX_CONTENT))
