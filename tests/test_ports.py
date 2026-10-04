"""Protocol shape and canonical boundary compatibility, with no real adapters.

Transactional, lease and effect semantics require A3/A5 conformance/fault tests;
these declaration checks deliberately do not claim to prove those semantics.
"""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
from typing import get_type_hints

import pytest

from radar import contracts, ports
from radar.ports import interfaces

PROTOCOLS = [getattr(interfaces, name) for name in (
    'UnitOfWork', 'Outbox', 'Ledger', 'LeaseStore', 'BlobStore', 'TaskQueue',
    'UserInterface', 'StructuredLLM', 'Clock', 'Secrets', 'Capabilities', 'FxRates',
)]


@pytest.mark.parametrize('port', PROTOCOLS)
def test_ports_are_protocols_with_resolvable_annotations(port):
    assert port._is_protocol
    assert port._is_runtime_protocol
    for name, member in vars(port).items():
        if callable(member) and not name.startswith('_'):
            assert get_type_hints(member)
            if port is not ports.Clock:
                owner = inspect.signature(member).parameters['owner_ref']
                assert owner.kind is inspect.Parameter.KEYWORD_ONLY
                assert owner.default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        port()


class FakeUI:
    def __init__(self):
        self.operations = []

    def send(self, *, owner_ref, conversation_ref, text, buttons=()):
        self.operations.append(('send', owner_ref, conversation_ref, text, buttons))
        return ports.UIMessage('message:sent', conversation_ref)

    def edit(self, *, owner_ref, conversation_ref, message_ref, text, buttons=()):
        self.operations.append(('edit', owner_ref, conversation_ref, message_ref, text, buttons))
        return ports.UIMessage(message_ref, conversation_ref)

    def answer_callback(self, *, owner_ref, callback_ref, text=None, alert=False):
        self.operations.append(('callback', owner_ref, callback_ref, text, alert))

    def send_document(self, *, owner_ref, conversation_ref, document, filename, caption=None):
        self.operations.append(('document', owner_ref, conversation_ref, document, filename, caption))
        return ports.UIMessage('message:document', conversation_ref)


def test_ui_fake_implements_all_operations_with_owner_scope():
    ui: ports.UserInterface = FakeUI()
    assert isinstance(ui, ports.UserInterface)
    buttons = ((ports.InlineButton('Approve', 'action:opaque'),),)
    assert ui.send(owner_ref='owner:alice', conversation_ref='chat:alice',
                   text='Synthetic opportunity', buttons=buttons).message_ref == 'message:sent'
    ui.edit(owner_ref='owner:alice', conversation_ref='chat:alice',
            message_ref='message:sent', text='Updated')
    ui.answer_callback(owner_ref='owner:alice', callback_ref='callback:abc', text='Accepted')
    ui.send_document(owner_ref='owner:alice', conversation_ref='chat:alice',
                     document=ports.BlobPointer('reports/test', 'a' * 64), filename='report.json')
    assert len(ui.operations) == 4
    assert all(item[1] == 'owner:alice' for item in ui.operations)
    assert not isinstance(object(), ports.UserInterface)


def test_dto_identity_distinguishes_operation_and_transport_message():
    accepted = ports.AcceptedCommand('operation:first', 'message:first', False)
    assert accepted.operation_id != accepted.message_id
    with pytest.raises(FrozenInstanceError):
        accepted.operation_id = 'operation:changed'


def test_receipt_secondary_indexes_are_immutable_and_backwards_compatible():
    original = ports.Receipt('telegram', 'event:update', 'a' * 64)
    assert original.idempotency_refs == ()
    refs = ('event:update', 'action:opaque')
    receipt = ports.Receipt('telegram', 'event:update', 'a' * 64, refs)
    assert receipt.idempotency_refs == refs
    with pytest.raises(FrozenInstanceError):
        receipt.idempotency_refs = ('action:changed',)
    with pytest.raises(TypeError):
        receipt.idempotency_refs[0] = 'action:changed'
    assert get_type_hints(ports.Receipt)['idempotency_refs'] == tuple[str, ...]


def test_accept_command_declares_secondary_index_atomicity_not_runtime_proof():
    contract = inspect.getdoc(ports.UnitOfWork.accept_command)
    assert 'ALL receipt.idempotency_refs' in contract
    assert 'owner-scoped transaction' in contract
    assert 'different operation raises ConditionalConflict' in contract
    assert 'writes nothing' in contract
    assert 'original IDs without new work' in contract


def test_control_document_reuses_canonical_envelope_without_duplicated_schema():
    root = Path(__file__).resolve().parents[1]
    path = sorted((root / 'contracts/examples/valid/envelope.v1').glob('*.json'))[0]
    envelope = json.loads(path.read_text(encoding='utf-8'))
    contracts.validate('envelope.v1', envelope)
    entry = ports.OutboxEntry(envelope, 'browser.fifo', 'owner:fixture:domain:fixture')
    assert entry.envelope is envelope
    assert 'lease' not in entry.envelope
    # An adapter, not the protocol, owns boundary validation.
    invalid = dict(envelope, unknown_field=True)
    from jsonschema import ValidationError
    with pytest.raises(ValidationError):
        contracts.validate('envelope.v1', invalid)


def test_secret_envelope_delivery_and_lease_repr_redact_sensitive_values():
    marker = 'SECRET_DO_NOT_LOG'
    assert marker not in repr(ports.SecretValue(marker.encode()))
    assert marker not in repr(ports.QueueDelivery({'payload': marker}, marker))
    assert marker not in repr(ports.OutboxEntry({'payload': marker}, 'results'))
    lease = ports.Lease('whatsapp:fixture', 'worker:one', marker,
                        datetime(2026, 10, 3, tzinfo=timezone.utc), 1)
    assert marker not in repr(lease)
    # Explicit unwrapping remains possible for the actual adapter.
    assert ports.SecretValue(marker.encode()).value == marker.encode()


def test_uncertainty_is_a_state_not_a_retry_exception():
    assert ports.LedgerState.SEND_UNCERTAIN.value == 'send_uncertain'
    assert not issubclass(ports.LedgerState, Exception)
    assert set(ports.LedgerState) == {
        ports.LedgerState.PROPOSED, ports.LedgerState.APPROVED,
        ports.LedgerState.DISPATCH_COMMITTED, ports.LedgerState.PROVIDER_CONFIRMED,
        ports.LedgerState.SEND_UNCERTAIN,
    }
