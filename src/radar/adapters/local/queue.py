"""Bounded SQLite-backed fake queue: publish, visibility/replay and durable ACK."""
from datetime import timedelta
from uuid import uuid4

from radar.ports.types import ConditionalConflict, QueueDelivery
from .codec import dumps, loads
from .sqlite import parse, stamp
from .wire import validate_envelope


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
            count = self.store.db.execute('SELECT count(*) FROM queue WHERE acked=0').fetchone()[0]
            if count >= self.capacity:
                raise OverflowError('queue_capacity')
            self.store.db.execute('INSERT INTO queue(owner,msg,destination,entry,visible) VALUES(?,?,?,?,?)',(owner_ref,msg,entry.destination,dumps(entry),stamp(self.clock.now())))
        return msg

    def receive(self, *, owner_ref, destination, limit):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('bounded_limit')
        with self.store.transaction():
            rows = self.store.db.execute('SELECT seq,entry FROM queue WHERE owner=? AND destination=? AND acked=0 AND visible<=? ORDER BY seq LIMIT ?', (owner_ref,destination,stamp(self.clock.now()),limit)).fetchall()
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
