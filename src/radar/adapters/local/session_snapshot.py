"""Explicit operator-only, consistent protocol-session copy; no login/network.

SQLite backup incorporates committed WAL data. Never copy an open DB via raw
file copying, and never import messages.db/media from an unrelated bridge.
The resulting SQLite credentials are plaintext inside an owner-private folder;
they must not enter Git, build context, logs or persistent Docker image layers.
"""
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
import stat
from time import monotonic

from .private_vault import _permissions, create_private_directory


class SnapshotError(ValueError):
    """Static operator diagnostics only, never credentials or DB contents."""


def snapshot_session(*, source, destination, authorize_copy=False,
                     max_bytes=67108864, timeout_seconds=30):
    if (authorize_copy is not True or type(max_bytes) is not int or not 1 <= max_bytes <= 268435456
            or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120):
        raise SnapshotError('session_copy_authorization_or_budget')
    source, destination = Path(source).absolute(),Path(destination).absolute()
    try:
        info = source.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SnapshotError('session_source_not_regular')
        if any(path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()) for path in (source,*source.parents)):
            raise SnapshotError('session_source_link_rejected')
        if not info.st_size or info.st_size > max_bytes:
            raise SnapshotError('session_source_size')
        # Trusted operator chooses one NEW directory; do not tighten, replace or
        # remove an existing bridge directory. Source remains read-only.
        create_private_directory(destination)
        path = destination/'whatsmeow.db'
        fd = os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        os.close(fd)
        _permissions(path)
        deadline = monotonic()+timeout_seconds
        with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=5) as reader:
            reader.execute('PRAGMA query_only=ON')
            if reader.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='whatsmeow_device'").fetchone() != (1,):
                raise SnapshotError('protocol_session_required')
            if reader.execute('SELECT count(*) FROM whatsmeow_device').fetchone() != (1,):
                raise SnapshotError('single_linked_device_required')
            page_size = reader.execute('PRAGMA page_size').fetchone()[0]
            def progress(status,remaining,total):
                if monotonic()>=deadline or total*page_size>max_bytes:
                    raise SnapshotError('session_copy_budget_exhausted')
            with sqlite3.connect(path,timeout=5) as writer:
                reader.backup(writer,pages=256,progress=progress,sleep=.01)
                if writer.execute('PRAGMA quick_check').fetchone() != ('ok',):
                    raise SnapshotError('session_snapshot_integrity')
        _permissions(path)
        digest = sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(65536),b''):
                digest.update(chunk)
        return {'copied':True,'network_used':False,'protocol_only':True,
            'bytes':path.stat().st_size,'sha256':digest.hexdigest()}
    except SnapshotError:
        raise
    except Exception:
        # Failed copies are not activated and are retained privately for audit.
        raise SnapshotError('session_snapshot_failed') from None
