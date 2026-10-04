from pathlib import Path
import sqlite3

import pytest

from radar.adapters.local.session_snapshot import snapshot_session, SnapshotError


def source(path):
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE whatsmeow_device(synthetic_key TEXT)')
    db.execute("INSERT INTO whatsmeow_device VALUES('synthetic credential marker')")
    db.commit()
    return db


def test_snapshot_contains_committed_wal_not_uncommitted_changes(tmp_path):
    path = tmp_path/'protocol.db'
    live = source(path)
    live.execute("UPDATE whatsmeow_device SET synthetic_key='uncommitted'")
    destination = tmp_path/'private-copy'
    result = snapshot_session(source=path,destination=destination,authorize_copy=True)
    with sqlite3.connect(destination/'whatsmeow.db') as copy:
        assert copy.execute('SELECT synthetic_key FROM whatsmeow_device').fetchone()==('synthetic credential marker',)
    assert result['protocol_only'] and not result['network_used']
    assert 'synthetic credential marker' not in str(result)
    live.rollback()
    live.close()


def test_snapshot_never_overwrites_and_no_implicit_authorization(tmp_path):
    path = tmp_path/'protocol.db'
    live = source(path)
    destination = tmp_path/'existing'
    destination.mkdir()
    with pytest.raises(SnapshotError):
        snapshot_session(source=path,destination=destination)
    with pytest.raises(SnapshotError):
        snapshot_session(source=path,destination=destination,authorize_copy=True)
    assert not (destination/'whatsmeow.db').exists()
    live.close()


def test_messages_database_not_accepted_as_protocol(tmp_path):
    path = tmp_path/'messages.db'
    with sqlite3.connect(path) as db: db.execute('CREATE TABLE messages(body TEXT)')
    with pytest.raises(SnapshotError,match='protocol_session_required'):
        snapshot_session(source=path,destination=tmp_path/'copy',authorize_copy=True)
