"""Compact control index + gzip/age blobs; no network or automatic authority."""
from base64 import b64decode, b64encode
from dataclasses import asdict
from datetime import timedelta
import gzip
from hashlib import sha256
import json
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit
import zlib

from radar.application.research.cache import (
    MAX_PAGE_BYTES, CacheBinding, CacheError, CachePolicy, CacheRecord, CachedRecord,
    CacheView, PageResult, Reservation, fingerprint, opaque,
)
from radar.domain.core import utc
from radar.ports.types import BlobPointer
from .sqlite import parse, stamp


MAX_FRAME = 1047552


def canonical_url(url):
    """Conservative adapter-bound identity, NOT fetch/SSRF authorization."""
    try:
        if not isinstance(url, str) or not 1 <= len(url.encode()) <= 8192 or any(ord(c) <= 32 for c in url):
            raise ValueError()
        parts = urlsplit(url)
        if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            raise ValueError()
        host = parts.hostname.encode('idna').decode().lower()
        if ':' in host:
            host = '[' + host + ']'
        port = parts.port
        if port is not None and port != (443 if parts.scheme.lower() == 'https' else 80):
            host += ':' + str(port)
        return urlunsplit((parts.scheme.lower(), host, parts.path or '/', parts.query, ''))
    except (ValueError, UnicodeError):
        raise CacheError('invalid_cache_url') from None


DDL = '''
CREATE TABLE IF NOT EXISTS research_cache_streams(owner TEXT,ref TEXT,binding TEXT,version INTEGER,active_pass TEXT,expires TEXT,cursor TEXT,cursor_hash TEXT,report TEXT,report_hash TEXT,position INTEGER DEFAULT 0,report_expires TEXT,PRIMARY KEY(owner,ref));
CREATE TABLE IF NOT EXISTS research_cache_passes(owner TEXT,ref TEXT,cache TEXT,mode TEXT,policy TEXT,inspected INTEGER DEFAULT 0,skipped INTEGER DEFAULT 0,new INTEGER DEFAULT 0,pages INTEGER DEFAULT 0,requests INTEGER DEFAULT 0,bytes INTEGER DEFAULT 0,PRIMARY KEY(owner,ref));
CREATE TABLE IF NOT EXISTS research_cache_requests(owner TEXT,ref TEXT,cache TEXT,pass TEXT,max_bytes INTEGER,state TEXT,hash TEXT,result TEXT,PRIMARY KEY(owner,ref));
CREATE INDEX IF NOT EXISTS research_cache_inflight ON research_cache_requests(owner,cache,state);
CREATE TABLE IF NOT EXISTS research_cache_records(owner TEXT,ref TEXT,origin TEXT,url_hash TEXT,body_hash TEXT,pointer TEXT,ordinal INTEGER,observed TEXT,expires TEXT,PRIMARY KEY(owner,ref),UNIQUE(owner,origin,url_hash,body_hash));
CREATE INDEX IF NOT EXISTS research_cache_record_expiry ON research_cache_records(owner,expires);
CREATE TABLE IF NOT EXISTS research_cache_seen(owner TEXT,cache TEXT,record TEXT,PRIMARY KEY(owner,cache,record));
CREATE TABLE IF NOT EXISTS research_cache_pending(owner TEXT,cache TEXT,position INTEGER,record TEXT,PRIMARY KEY(owner,cache,position));
'''


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _pointer(value):
    return None if value is None else BlobPointer(**json.loads(value))


def _policy(value):
    document = asdict(value)
    document['deadline'] = stamp(value.deadline)
    return _json(document)


class SQLiteResearchCache:
    def __init__(self, *, store, vault, authorize, max_records_per_owner=5000,
                 max_streams_per_owner=128):
        if vault is None or not callable(authorize):
            raise CacheError('private_cache_dependencies_required')
        if any(type(v) is not int or not 1 <= v <= 100000 for v in
               (max_records_per_owner, max_streams_per_owner)):
            raise CacheError('invalid_cache_storage_limit')
        self.store, self.vault, self.authorize = store, vault, authorize
        self.max_records, self.max_streams = max_records_per_owner, max_streams_per_owner
        store.db.executescript(DDL)

    def _check(self, binding, now):
        if not isinstance(binding, CacheBinding):
            raise CacheError('cache_binding_required')
        utc(now)
        try:
            permitted = self.authorize(binding, now) is True
            self.store._active(binding.owner_ref)
            permitted = permitted and self.store.authorize_actor(owner_ref=binding.owner_ref, actor_ref=binding.actor_ref)
            permitted = permitted and bool(self.store.current_consent(binding.owner_ref, binding.actor_ref))
        except Exception:
            raise CacheError('cache_access_denied') from None
        if not permitted:
            raise CacheError('cache_access_denied')

    def _identity(self, binding):
        return 'cache:c' + fingerprint(binding.owner_ref, _json(asdict(binding)).encode())

    def _origin(self, binding):
        return fingerprint(binding.owner_ref, _json([binding.source_ref, binding.account_ref,
                                                   binding.session_ref, binding.session_version]).encode())

    def _row(self, binding):
        row = self.store.db.execute('SELECT * FROM research_cache_streams WHERE owner=? AND ref=?',
                                    (binding.owner_ref, self._identity(binding))).fetchone()
        if row is None or row[2] != _json(asdict(binding)):
            raise CacheError('unknown_cache_binding')
        return row

    def _pass(self, row):
        return self.store.db.execute('SELECT * FROM research_cache_passes WHERE owner=? AND ref=?',
                                     (row[0], row[4])).fetchone()

    def _view(self, row, now):
        run = self._pass(row)
        policy = json.loads(run[4])
        count = self.store.db.execute('SELECT count(*) FROM research_cache_pending WHERE owner=? AND cache=?',
                                      (row[0], row[1])).fetchone()[0]
        state = 'ready'
        if parse(row[5]) <= now: state = 'expired'
        elif parse(policy['deadline']) <= now: state = 'deadline'
        elif run[7] >= policy['max_items']: state = 'item_limit'
        elif self.store.db.execute('SELECT 1 FROM research_cache_requests WHERE owner=? AND cache=? AND state=? LIMIT 1',
                                  (row[0], row[1], 'reserved')).fetchone(): state = 'in_flight'
        elif run[3] == 'cache-only': state = 'cache_only'
        elif run[8] >= policy['max_pages']: state = 'page_limit'
        elif run[9] >= policy['max_requests']: state = 'request_limit'
        elif run[10] >= policy['max_bytes']: state = 'byte_limit'
        return CacheView(row[1], run[1], row[3], _pointer(row[6]), _pointer(row[8]),
                         count, run[5], run[6], run[7], run[8], run[9], run[10], parse(row[5]), state)

    def current(self, binding, *, now):
        self._check(binding, now)
        return self._view(self._row(binding), now)

    def open(self, binding, *, pass_ref, mode, policy, now, expected_version=None):
        self._check(binding, now)
        opaque(pass_ref)
        if mode not in ('continue', 'refresh', 'cache-only') or not isinstance(policy, CachePolicy):
            raise CacheError('invalid_cache_mode_or_policy')
        if policy.deadline <= now:
            raise CacheError('cache_deadline')
        owner, ref, document = binding.owner_ref, self._identity(binding), _json(asdict(binding))
        with self.store.transaction():
            self._check(binding, now)
            row = self.store.db.execute('SELECT * FROM research_cache_streams WHERE owner=? AND ref=?', (owner, ref)).fetchone()
            previous = self.store.db.execute('SELECT * FROM research_cache_passes WHERE owner=? AND ref=?', (owner, pass_ref)).fetchone()
            if previous:
                if not row or row[4] != pass_ref or previous[2:5] != (ref, mode, _policy(policy)):
                    raise CacheError('cache_pass_replay_mismatch')
                return self._view(row, now)
            if self.store.db.execute('SELECT count(*) FROM research_cache_passes WHERE owner=?', (owner,)).fetchone()[0] >= self.max_records:
                raise CacheError('cache_pass_quota')
            if row:
                if type(expected_version) is not int or expected_version != row[3]:
                    raise CacheError('cache_version_conflict')
                if self.store.db.execute('SELECT 1 FROM research_cache_requests WHERE owner=? AND cache=? AND state=? LIMIT 1',
                                         (owner, ref, 'reserved')).fetchone():
                    raise CacheError('cache_request_uncertain')
                if parse(row[5]) <= now and mode == 'continue':
                    raise CacheError('cache_expired_refresh_required')
                if mode == 'refresh':
                    self.store.db.execute('DELETE FROM research_cache_pending WHERE owner=? AND cache=?', (owner, ref))
                    self.store.db.execute('UPDATE research_cache_streams SET cursor=NULL,cursor_hash=NULL,report=NULL,report_hash=NULL,report_expires=NULL WHERE owner=? AND ref=?', (owner, ref))
                expires = stamp(now + timedelta(seconds=policy.retention_seconds)) if mode == 'refresh' else row[5]
                self.store.db.execute('UPDATE research_cache_streams SET active_pass=?,version=version+1,expires=? WHERE owner=? AND ref=?',
                                      (pass_ref, expires, owner, ref))
            else:
                count = self.store.db.execute('SELECT count(*) FROM research_cache_streams WHERE owner=?', (owner,)).fetchone()[0]
                if count >= self.max_streams:
                    raise CacheError('cache_stream_quota')
                self.store.db.execute('INSERT INTO research_cache_streams(owner,ref,binding,version,active_pass,expires) VALUES(?,?,?,1,?,?)',
                                      (owner, ref, document, pass_ref, stamp(now + timedelta(seconds=policy.retention_seconds))))
            self.store.db.execute('INSERT INTO research_cache_passes(owner,ref,cache,mode,policy) VALUES(?,?,?,?,?)',
                                  (owner, pass_ref, ref, mode, _policy(policy)))
            return self._view(self._row(binding), now)

    def reserve(self, binding, *, pass_ref, request_ref, expected_version, now, max_bytes=MAX_PAGE_BYTES):
        self._check(binding, now)
        opaque(request_ref)
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_PAGE_BYTES:
            raise CacheError('invalid_cache_read_limit')
        owner = binding.owner_ref
        with self.store.transaction():
            self._check(binding, now)
            row = self._row(binding)
            old = self.store.db.execute('SELECT * FROM research_cache_requests WHERE owner=? AND ref=?', (owner, request_ref)).fetchone()
            if old:
                if old[2:4] != (row[1], pass_ref) or row[4] != pass_ref:
                    raise CacheError('cache_request_replay_mismatch')
                return Reservation(self._view(row, now), request_ref, old[4], True, old[5])
            if row[4] != pass_ref or row[3] != expected_version:
                raise CacheError('cache_version_conflict')
            view = self._view(row, now)
            if view.state != 'ready' or view.pending_items:
                raise CacheError('cache_read_blocked')
            run = self._pass(row)
            policy = json.loads(run[4])
            if self.store.db.execute('SELECT count(*) FROM research_cache_requests WHERE owner=?', (owner,)).fetchone()[0] >= self.max_records * 10:
                raise CacheError('cache_request_quota')
            allowance = min(max_bytes, policy['max_bytes'] - run[10])
            self.store.db.execute('UPDATE research_cache_passes SET requests=requests+1,pages=pages+1,bytes=bytes+? WHERE owner=? AND ref=?',
                                  (allowance, owner, pass_ref))
            self.store.db.execute('INSERT INTO research_cache_requests VALUES(?,?,?,?,?,\'reserved\',NULL,NULL)',
                                  (owner, request_ref, row[1], pass_ref, allowance))
            self.store.db.execute('UPDATE research_cache_streams SET version=version+1 WHERE owner=? AND ref=?', (owner, row[1]))
            return Reservation(self._view(self._row(binding), now), request_ref, allowance, False, 'reserved')

    def _seal(self, binding, frame):
        plain = _json(frame).encode()
        if len(plain) > MAX_FRAME:
            raise CacheError('cache_frame_limit')
        compressed = gzip.compress(plain, compresslevel=3, mtime=0)
        pointer = self.vault.seal(owner_ref=binding.owner_ref, plaintext=compressed)
        if pointer.recipient_scope != 'worker:research':
            raise CacheError('cache_vault_scope')
        return _json(asdict(pointer))

    def _open(self, binding, pointer):
        try:
            if pointer.recipient_scope != 'worker:research':
                raise CacheError('cache_vault_scope')
            compressed = self.vault.open(owner_ref=binding.owner_ref, pointer=pointer)
            inflater = zlib.decompressobj(31)
            plain = inflater.decompress(compressed, MAX_FRAME + 1)
            if len(plain) > MAX_FRAME or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
                raise CacheError('cache_frame_limit')
            return json.loads(plain)
        except CacheError:
            raise
        except Exception:
            raise CacheError('private_cache_integrity') from None

    def _record(self, owner, ref):
        row = self.store.db.execute('SELECT * FROM research_cache_records WHERE owner=? AND ref=?', (owner, ref)).fetchone()
        if not row:
            raise CacheError('unknown_cached_record')
        return CachedRecord(row[1], row[3], row[4], _pointer(row[5]), row[6], parse(row[7]), parse(row[8]))

    def _drain(self, binding, row, now):
        run = self._pass(row)
        allowance = json.loads(run[4])['max_items'] - run[7]
        rows = self.store.db.execute('SELECT position,record FROM research_cache_pending WHERE owner=? AND cache=? ORDER BY position LIMIT ?',
                                    (row[0], row[1], max(0, allowance))).fetchall()
        records = tuple(self._record(row[0], r[1]) for r in rows)
        if any(record.expires_at <= now for record in records):
            raise CacheError('stale_pending_refresh_required')
        for position, _ in rows:
            self.store.db.execute('DELETE FROM research_cache_pending WHERE owner=? AND cache=? AND position=?', (row[0], row[1], position))
        self.store.db.execute('UPDATE research_cache_passes SET new=new+? WHERE owner=? AND ref=?', (len(records), row[0], row[4]))
        return records

    def drain(self, binding, *, pass_ref, expected_version, now):
        self._check(binding, now)
        with self.store.transaction():
            self._check(binding, now)
            row = self._row(binding)
            if row[4] != pass_ref or row[3] != expected_version:
                raise CacheError('cache_version_conflict')
            if parse(row[5]) <= now or parse(json.loads(self._pass(row)[4])['deadline']) <= now:
                raise CacheError('cache_deadline')
            if self.store.db.execute('SELECT 1 FROM research_cache_requests WHERE owner=? AND cache=? AND state=? LIMIT 1', (row[0], row[1], 'reserved')).fetchone():
                raise CacheError('cache_request_uncertain')
            records = self._drain(binding, row, now)
            self.store.db.execute('UPDATE research_cache_streams SET version=version+1 WHERE owner=? AND ref=?', (row[0], row[1]))
            return PageResult(records, self._view(self._row(binding), now))

    def commit_page(self, binding, *, pass_ref, request_ref, expected_version, records,
                    continuation, received_bytes, now, report_bytes=None):
        self._check(binding, now)
        if not isinstance(records, tuple) or len(records) > 5000 or any(not isinstance(r, CacheRecord) for r in records):
            raise CacheError('invalid_cache_page')
        if continuation is not None and (not isinstance(continuation, bytes) or len(continuation) > 4096):
            raise CacheError('invalid_private_continuation')
        if report_bytes is not None and (not isinstance(report_bytes, bytes) or len(report_bytes) > MAX_PAGE_BYTES):
            raise CacheError('invalid_private_report')
        minimum = sum(len(r.content) for r in records)
        captured_size = minimum + sum(len(canonical_url(r.url).encode()) for r in records)
        captured_size += len(continuation or b'') + len(report_bytes or b'')
        if captured_size > MAX_PAGE_BYTES:
            raise CacheError('cache_page_limit')
        if type(received_bytes) is not int or not minimum <= received_bytes <= MAX_PAGE_BYTES:
            raise CacheError('invalid_cache_byte_count')
        if any(r.observed_at > now for r in records):
            raise CacheError('invalid_cache_observation')
        hashes = [(fingerprint(binding.owner_ref, canonical_url(r.url).encode()), sha256(r.content).hexdigest(), stamp(r.observed_at)) for r in records]
        receipt_hash = fingerprint(binding.owner_ref, _json([hashes,
            None if continuation is None else fingerprint(binding.owner_ref, continuation),
            None if report_bytes is None else fingerprint(binding.owner_ref, report_bytes), received_bytes]).encode())
        owner, origin = binding.owner_ref, self._origin(binding)
        with self.store.transaction():
            self._check(binding, now)
            row = self._row(binding)
            request = self.store.db.execute('SELECT * FROM research_cache_requests WHERE owner=? AND ref=?', (owner, request_ref)).fetchone()
            if not request or request[2:4] != (row[1], pass_ref) or row[4] != pass_ref:
                raise CacheError('unknown_cache_request')
            if request[5] == 'committed':
                if request[6] != receipt_hash:
                    raise CacheError('cache_page_replay_mismatch')
                return PageResult(tuple(self._record(owner, ref) for ref in json.loads(request[7])), self._view(row, now), True)
            if request[5] != 'reserved' or row[3] != expected_version:
                raise CacheError('cache_version_conflict')
            run = self._pass(row)
            policy = json.loads(run[4])
            if parse(policy['deadline']) <= now or parse(row[5]) <= now:
                raise CacheError('cache_deadline')
            if received_bytes > request[4]:
                raise CacheError('cache_byte_limit')
            new_bodies, selected, skipped = [], [], 0
            encountered = set()
            for record, (url_hash, body_hash, observed) in zip(records, hashes):
                pair = (url_hash, body_hash)
                if pair in encountered:
                    skipped += 1
                    continue
                encountered.add(pair)
                stored = self.store.db.execute('SELECT * FROM research_cache_records WHERE owner=? AND origin=? AND url_hash=? AND body_hash=?',
                                              (owner, origin, url_hash, body_hash)).fetchone()
                expires = stamp(record.observed_at + timedelta(seconds=policy['retention_seconds']))
                if parse(expires) <= now:
                    raise CacheError('stale_cache_observation')
                if stored:
                    ref = stored[1]
                    seen = self.store.db.execute('SELECT 1 FROM research_cache_seen WHERE owner=? AND cache=? AND record=?', (owner, row[1], ref)).fetchone()
                    self.store.db.execute('UPDATE research_cache_records SET observed=?,expires=? WHERE owner=? AND ref=?', (observed, expires, owner, ref))
                    if seen:
                        skipped += 1
                        continue
                else:
                    ref = 'record:r' + uuid4().hex
                    new_bodies.append((ref, record, url_hash, body_hash, observed, expires))
                selected.append(ref)
            count = self.store.db.execute('SELECT count(*) FROM research_cache_records WHERE owner=?', (owner,)).fetchone()[0]
            if count + len(new_bodies) > self.max_records:
                raise CacheError('cache_record_quota')
            if new_bodies:
                pointer = self._seal(binding, {'version': 1, 'kind': 'records', 'origin': origin,
                                             'records': [{'url': canonical_url(r.url), 'body': b64encode(r.content).decode()} for _, r, *_ in new_bodies]})
                for ordinal, (ref, _, url_hash, body_hash, observed, expires) in enumerate(new_bodies):
                    self.store.db.execute('INSERT INTO research_cache_records VALUES(?,?,?,?,?,?,?,?,?)',
                                          (owner, ref, origin, url_hash, body_hash, pointer, ordinal, observed, expires))
            for position, ref in enumerate(selected, row[10]):
                self.store.db.execute('INSERT OR IGNORE INTO research_cache_seen VALUES(?,?,?)', (owner, row[1], ref))
                self.store.db.execute('INSERT INTO research_cache_pending VALUES(?,?,?,?)', (owner, row[1], position, ref))
            cursor_hash = fingerprint(owner, continuation) if continuation is not None else None
            cursor = row[6] if cursor_hash == row[7] else (self._seal(binding, {'version': 1, 'kind': 'cursor', 'cache': row[1], 'body': b64encode(continuation).decode()}) if continuation is not None else None)
            report_hash = fingerprint(owner, report_bytes) if report_bytes is not None else row[9]
            report = row[8] if report_hash == row[9] else self._seal(binding, {'version': 1, 'kind': 'report', 'cache': row[1], 'body': b64encode(report_bytes).decode()})
            report_expires = stamp(now + timedelta(seconds=policy['retention_seconds'])) if report_bytes is not None else row[11]
            self._check(binding, now)
            self.store.db.execute('UPDATE research_cache_passes SET inspected=inspected+?,skipped=skipped+?,bytes=bytes-?+? WHERE owner=? AND ref=?',
                                  (len(records), skipped, request[4], received_bytes, owner, pass_ref))
            self.store.db.execute('UPDATE research_cache_streams SET cursor=?,cursor_hash=?,report=?,report_hash=?,report_expires=?,expires=?,position=position+?,version=version+1 WHERE owner=? AND ref=?',
                                  (cursor, cursor_hash, report, report_hash, report_expires, stamp(now + timedelta(seconds=policy['retention_seconds'])), len(selected), owner, row[1]))
            delivered = self._drain(binding, self._row(binding), now)
            self.store.db.execute('UPDATE research_cache_requests SET state=\'committed\',hash=?,result=? WHERE owner=? AND ref=?',
                                  (receipt_hash, _json([r.record_ref for r in delivered]), owner, request_ref))
            return PageResult(delivered, self._view(self._row(binding), now))

    def abandon(self, binding, *, request_ref, expected_version, now):
        """Explicitly mark unknown IO usage; keep full reserved bytes charged."""
        self._check(binding, now)
        with self.store.transaction():
            self._check(binding, now)
            row = self._row(binding)
            if row[3] != expected_version:
                raise CacheError('cache_version_conflict')
            count = self.store.db.execute('UPDATE research_cache_requests SET state=\'uncertain\' WHERE owner=? AND ref=? AND cache=? AND state=\'reserved\'',
                                         (binding.owner_ref, request_ref, row[1])).rowcount
            if count != 1:
                raise CacheError('unknown_cache_request')
            self.store.db.execute('UPDATE research_cache_streams SET version=version+1 WHERE owner=? AND ref=?', (row[0], row[1]))
            return self._view(self._row(binding), now)

    def recover_page(self, binding, *, pass_ref, request_ref, now):
        """Recover committed refs after host-checkpoint crash, without I/O.

        A receipt proves neither current authority nor permission to reissue an
        uncertain request. Missing receipts return None without reserving quota.
        """
        self._check(binding, now)
        opaque(pass_ref)
        opaque(request_ref)
        with self.store.transaction():
            self._check(binding, now)
            request = self.store.db.execute('SELECT * FROM research_cache_requests WHERE owner=? AND ref=?',
                                            (binding.owner_ref, request_ref)).fetchone()
            if request is None:
                return None
            row = self._row(binding)
            if row[4] != pass_ref:
                raise CacheError('cache_pass_replay_mismatch')
            if parse(row[5]) <= now:
                raise CacheError('cache_expired_refresh_required')
            if request[2:4] != (row[1], pass_ref):
                raise CacheError('cache_request_replay_mismatch')
            if request[5] != 'committed':
                raise CacheError('cache_request_uncertain')
            records = tuple(self._record(binding.owner_ref, ref) for ref in json.loads(request[7]))
            if any(record.expires_at <= now for record in records):
                raise CacheError('stale_cached_record')
            self._check(binding, now)
            return PageResult(records, self._view(row, now), True)

    def lookup(self, binding, *, url, now, mode='continue'):
        """None means missing/stale/refresh, never authority to fetch."""
        self._check(binding, now)
        if mode not in ('continue', 'refresh', 'cache-only'):
            raise CacheError('invalid_cache_mode_or_policy')
        if mode == 'refresh': return None
        cache = self._row(binding)
        if parse(cache[5]) <= now: return None
        key = fingerprint(binding.owner_ref, canonical_url(url).encode())
        row = self.store.db.execute('SELECT r.ref FROM research_cache_seen s JOIN research_cache_records r ON r.owner=s.owner AND r.ref=s.record WHERE s.owner=? AND s.cache=? AND r.url_hash=? AND r.expires>? ORDER BY r.observed DESC,r.ref LIMIT 1',
                                    (binding.owner_ref, cache[1], key, stamp(now))).fetchone()
        return self._record(binding.owner_ref, row[0]) if row else None

    def seen_url(self, binding, *, url, now):
        """Fresh owner-bound URL check before host creates another source blob.

        Continue can skip the URL; refresh must deliberately bypass this hint to
        observe changed content. This does not claim the URL was fully covered.
        """
        return self.lookup(binding, url=url, now=now) is not None

    def read_record(self, binding, *, record_ref, now):
        self._check(binding, now)
        row = self._row(binding)
        if parse(row[5]) <= now:
            raise CacheError('cache_expired_refresh_required')
        if not self.store.db.execute('SELECT 1 FROM research_cache_seen WHERE owner=? AND cache=? AND record=?', (row[0], row[1], record_ref)).fetchone():
            raise CacheError('unknown_cached_record')
        record = self._record(row[0], record_ref)
        if record.expires_at <= now:
            raise CacheError('stale_cached_record')
        frame = self._open(binding, record.private_ref)
        try:
            item = frame['records'][record.ordinal]
            body = b64decode(item['body'], validate=True)
            if frame['version'] != 1 or frame['kind'] != 'records' or frame['origin'] != self._origin(binding) or sha256(body).hexdigest() != record.content_sha256 or fingerprint(binding.owner_ref, canonical_url(item['url']).encode()) != record.url_sha256:
                raise ValueError()
            self._check(binding, now)
            return CacheRecord(item['url'], body, record.observed_at)
        except CacheError:
            raise
        except Exception:
            raise CacheError('private_cache_integrity') from None

    def _stream_content(self, binding, *, now, kind, column):
        self._check(binding, now)
        row = self._row(binding)
        if parse(row[5]) <= now:
            raise CacheError('cache_expired_refresh_required')
        if row[column] is None: return None
        if kind == 'report' and (row[11] is None or parse(row[11]) <= now):
            raise CacheError('stale_cached_report')
        frame = self._open(binding, _pointer(row[column]))
        try:
            if frame['version'] != 1 or frame['kind'] != kind or frame['cache'] != row[1]:
                raise ValueError()
            body = b64decode(frame['body'], validate=True)
            if fingerprint(binding.owner_ref, body) != row[column + 1]:
                raise ValueError()
            self._check(binding, now)
            return body
        except CacheError:
            raise
        except Exception:
            raise CacheError('private_cache_integrity') from None

    def continuation(self, binding, *, now):
        return self._stream_content(binding, now=now, kind='cursor', column=6)

    def report(self, binding, *, now):
        return self._stream_content(binding, now=now, kind='report', column=8)
