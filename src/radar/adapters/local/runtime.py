"""Programmatic synthetic integration; Telegram remains the commercial MVP UI."""
from datetime import datetime, timezone

from radar.adapters.telegram.webhook import Webhook
from radar.adapters.telegram.allowlist import PhoneAllowlist
from radar.application.local_flow import handle_worker_result, run_saved_search
from radar.ports.types import ConditionalConflict
from .queue import FakeQueue, relay_outbox, PERMANENT_REJECTIONS
from .sqlite import SQLiteStore
from .telegram import Directory, Idempotency, NoInvites, TelegramBridge
from .ui import FakeUI, deliver_alerts
from .worker import FakeWorker, decode_result


class FixedClock:
    def __init__(self, now=None):
        self.value = now or datetime(2026,10,3,tzinfo=timezone.utc)

    def now(self):
        return self.value


class LocalRuntime:
    def __init__(self, path, *, fixtures, synthetic_authorized=False, clock=None, failpoint=None,
                 phone_allowlist=None, synthetic_contact_owner_ref=None):
        if synthetic_authorized is not True:
            raise ValueError('explicit_synthetic_authorization_required')
        phones = phone_allowlist or PhoneAllowlist.from_json('{"schema_version":1,"entries":[]}')
        self.clock = clock or FixedClock()
        self.store = SQLiteStore(path,failpoint=failpoint)
        self.directory = Directory(self.store,synthetic_contact_owner_ref=synthetic_contact_owner_ref)
        self.bridge = TelegramBridge(self.store,self.directory)
        self.webhook = Webhook(secret='synthetic-local-secret',unit_of_work=self.bridge,
                               idempotency=Idempotency(self.store),users=self.directory,
                               invites=NoInvites(),phone_allowlist=phones,clock=self.clock.now)
        self.queue = FakeQueue(self.store,self.clock)
        try:
            self.worker = FakeWorker(self.store,self.clock,fixtures=fixtures,synthetic_authorized=synthetic_authorized)
        except BaseException:
            self.store.close()
            raise
        self.ui = FakeUI(self.store,self.directory)

    def relay(self, owner_ref):
        cursor = None
        while True:
            cursor = relay_outbox(self.store,self.queue,owner_ref=owner_ref,now=self.clock.now(),cursor=cursor,
                                  pause_on_capacity=True)
            if cursor is None:
                break

    def process_commands(self, owner_ref, *, search_ref, failpoint=lambda stage: None):
        deliveries = self.queue.receive(owner_ref=owner_ref,destination='commands.fifo',limit=10)
        for delivery in deliveries:
            def apply():
                command = delivery.envelope
                name = command['payload']['command']
                if name == 'buscar':
                    run_saved_search(self.store,owner_ref=owner_ref,search_ref=search_ref,command=command,now=self.clock.now())
                elif name == 'stop':
                    self.store.stop(owner_ref=owner_ref)
                else:
                    raise ConditionalConflict('unsupported')
            self._consume(owner_ref,delivery,apply,'after_command_application',failpoint)
        return len(deliveries)

    def process_workers(self, owner_ref, *, failpoint=lambda stage: None):
        deliveries = self.queue.receive(owner_ref=owner_ref,destination='browser.fifo',limit=10)
        for delivery in deliveries:
            self._consume(owner_ref,delivery,lambda: self.worker.execute(owner_ref=owner_ref,envelope=delivery.envelope),
                          'after_worker_result',failpoint)
        return len(deliveries)

    def process_results(self, owner_ref, *, failpoint=lambda stage: None):
        deliveries = self.queue.receive(owner_ref=owner_ref,destination='results',limit=10)
        for delivery in deliveries:
            def apply():
                result = decode_result(self.store,owner_ref=owner_ref,envelope=delivery.envelope)
                handle_worker_result(self.store,owner_ref=owner_ref,result=result,now=self.clock.now())
            self._consume(owner_ref,delivery,apply,'after_result_projection',failpoint)
        return len(deliveries)

    def _consume(self, owner, delivery, apply, completed_stage, failpoint):
        if self.queue.quarantined(owner_ref=owner,delivery=delivery):
            self.queue.acknowledge(owner_ref=owner,delivery=delivery)
            return
        try:
            apply()
        except ConditionalConflict as error:
            code = str(error)
            if code not in PERMANENT_REJECTIONS:
                raise
            self.queue.quarantine(owner_ref=owner,delivery=delivery,code=code)
            failpoint('after_quarantine')
        else:
            failpoint(completed_stage)
        # Neither the checkpoint nor the ACK lies inside the exception handler
        # for workflow failures: their failures must remain retryable/visible.
        self.queue.acknowledge(owner_ref=owner,delivery=delivery)

    def pump(self, owner_ref, *, search_ref, max_steps=100):
        for _ in range(max_steps):
            self.relay(owner_ref)
            commands = self.process_commands(owner_ref,search_ref=search_ref)
            workers = self.process_workers(owner_ref)
            self.relay(owner_ref)
            results = self.process_results(owner_ref)
            deliver_alerts(self.store,self.ui,owner_ref=owner_ref)
            if not (commands or workers or results):
                return
        raise RuntimeError('bounded_pump_exhausted')
