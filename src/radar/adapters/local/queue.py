"""Bounded SQLite-backed fake queue: publish, visibility/replay and durable ACK."""
from datetime import timedelta
from uuid import uuid4

from radar.ports.types import ConditionalConflict, QueueDelivery
from .codec import dumps, loads
from .sqlite import digest, stamp
from .wire import validate_envelope


# Only these terminal workflow rejections may be checkpointed and ACKed.
# Unknown conflicts, storage failures and injected crashes always propagate.
PERMANENT_REJECTIONS = frozenset({
    'actor_not_authorized', 'run_consent', 'command_expired',
    'owner_stopped_or_unknown', 'worker_cancelled_unauthorized_or_expired',
    'cancelled_or_stale_run', 'unsupported',
})


class FakeQueue:
    def __init__(self, store, clock, *, capacity=100, visibility=timedelta(seconds=30)):
        if not 1 <= capacity <= 1000 or visibility <= timedelta(0):
            raise ValueError('bounded_queue_required')
        self.store,self.clock,self.capacity,self.visibility = store,clock,capacity,visibility

    def publish(self, *, owner_ref, entry):
        validate_envelope(entry.envelope)
        if owner_ref != entry.envelope['owner_ref']:
            raise ConditionalConflict('queue_owner')
        msg = entry.envelope['message_id']
        with self.store.transaction():
            old = self.store.db.execute('SELECT entry FROM queue WHERE owner=? AND msg=?',(owner_ref,msg)).fetchone()
            if old:
                if old[0] != dumps(entry):
                    raise ConditionalConflict('queue_replay_content')
                return msg
            count = self.store.db.execute('SELECT count(*) FROM queue WHERE owner=? AND acked=0',(owner_ref,)).fetchone()[0]
            if count >= self.capacity:
                raise OverflowError('queue_capacity')
            self.store.db.execute('INSERT INTO queue(owner,msg,destination,entry,visible) VALUES(?,?,?,?,?)',(owner_ref,msg,entry.destination,dumps(entry),stamp(self.clock.now())))
        return msg

    def receive(self, *, owner_ref, destination, limit):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('bounded_limit')
        with self.store.transaction():
            # Legacy whole-second timestamps represent precisely second zero;
            # preserve their visibility without rewriting historical rows.
            now = self.clock.now()
            rows = self.store.db.execute('SELECT seq,entry FROM queue WHERE owner=? AND destination=? AND acked=0 AND (visible<=? OR visible=?) ORDER BY seq LIMIT ?', (owner_ref,destination,stamp(now),stamp(now).split('.')[0]+'Z',limit)).fetchall()
            deliveries = []
            for seq,entry in rows:
                token = str(uuid4())
                self.store.db.execute('UPDATE queue SET token=?,visible=? WHERE seq=?',(token,stamp(self.clock.now()+self.visibility),seq))
                deliveries.append(QueueDelivery(loads(entry).envelope,token))
            return tuple(deliveries)

    def acknowledge(self, *, owner_ref, delivery):
        if delivery.envelope['owner_ref'] != owner_ref:
            raise ConditionalConflict('ack_owner')
        updated = self.store.db.execute('UPDATE queue SET acked=1 WHERE owner=? AND msg=? AND token=? AND acked=0', (owner_ref,delivery.envelope['message_id'],delivery.acknowledgement_ref)).rowcount
        if not updated:
            raise ConditionalConflict('stale_ack')

    def _binding(self, owner, delivery):
        validate_envelope(delivery.envelope)
        if delivery.envelope['owner_ref'] != owner:
            raise ConditionalConflict('quarantine_owner')
        row = self.store.db.execute('SELECT entry,destination,token FROM queue WHERE owner=? AND msg=? AND acked=0',
            (owner,delivery.envelope['message_id'])).fetchone()
        if not row or row[2] != delivery.acknowledgement_ref or loads(row[0]).envelope != delivery.envelope:
            raise ConditionalConflict('quarantine_delivery_binding')
        return row[1], digest(delivery.envelope)

    def quarantined(self, *, owner_ref, delivery):
        with self.store.transaction():
            destination, content_hash = self._binding(owner_ref,delivery)
            old = self.store.db.execute('SELECT op,destination,hash FROM quarantine WHERE owner=? AND msg=?',
                (owner_ref,delivery.envelope['message_id'])).fetchone()
            if old and old != (delivery.envelope['operation_id'],destination,content_hash):
                raise ConditionalConflict('quarantine_replay_binding')
            return old is not None

    def quarantine(self, *, owner_ref, delivery, code):
        if code not in PERMANENT_REJECTIONS:
            raise ValueError('unknown_permanent_rejection')
        with self.store.transaction():
            destination, content_hash = self._binding(owner_ref,delivery)
            values = (delivery.envelope['operation_id'],destination,code,content_hash)
            old = self.store.db.execute('SELECT op,destination,code,hash FROM quarantine WHERE owner=? AND msg=?',
                (owner_ref,delivery.envelope['message_id'])).fetchone()
            if old:
                if old != values:
                    raise ConditionalConflict('quarantine_replay_binding')
                return
            self.store.db.execute('INSERT INTO quarantine VALUES(?,?,?,?,?,?,?)',
                (owner_ref,delivery.envelope['message_id'],*values,stamp(self.clock.now())))
            self.store.failpoint('before_quarantine_commit')

    def replay(self, *, owner_ref, destination):
        self.store.db.execute('UPDATE queue SET visible=? WHERE owner=? AND destination=? AND acked=0',(stamp(self.clock.now()),owner_ref,destination))


def relay_outbox(store, queue, *, owner_ref, now, limit=10, cursor=None, failpoint=lambda stage: None):
    page = store.pending(owner_ref=owner_ref,limit=limit,cursor=cursor)
    for entry in page.entries:
        queue.publish(owner_ref=owner_ref,entry=entry)
        failpoint('after_publish')
        store.mark_published(owner_ref=owner_ref,message_id=entry.envelope['message_id'],expected_version=entry.version,published_at=now)
    return page.next_cursor


def relay_insert(store, queue, *, owner_ref, event_name, now):
    """Only INSERT events wake repair; our own publication marks cannot recurse."""
    return relay_outbox(store,queue,owner_ref=owner_ref,now=now) if event_name == 'INSERT' else None
