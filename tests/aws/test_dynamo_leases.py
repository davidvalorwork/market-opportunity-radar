"""SDK+moto only: no AWS accounts, SDK credential discovery or live resources."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
import socket
from types import SimpleNamespace

import pytest

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError, ReadTimeoutError
    from botocore.stub import Stubber
    from moto import mock_aws
except ImportError as error:
    if os.environ.get('RADAR_REQUIRE_AWS_TESTS') == '1':
        raise RuntimeError('aws_test_dependencies_required') from error
    pytest.skip('Optional AWS SDK/moto absent; Dynamo conformance NOT executed', allow_module_level=True)

from radar.adapters.aws.dynamo_leases import DynamoLeaseStore
from radar.ports.interfaces import LeaseStore
from radar.ports.types import ConditionalConflict
from tests.conformance.lease_cases import (
    NOW, OTHER, OWNER, TTL, lease,
    test_expiration_boundary_allows_new_epoch_not_old_renewal,
    test_forged_lease_cannot_renew_or_release,
    test_lease_excludes_both_same_and_other_workers,
    test_release_reacquire_preserves_monotonic_epoch_across_restart,
    test_renew_fences_original_snapshot_and_survives_restart,
    test_same_session_ref_is_independent_per_owner,
)


TABLE = 'radar-control-synthetic'


def sdk_client(**config):
    # Explicit synthetic credentials bypass host profiles, SSO, web identity and IMDS.
    return boto3.client('dynamodb', region_name='us-east-1',
                        aws_access_key_id='synthetic-access',
                        aws_secret_access_key='synthetic-secret',
                        aws_session_token='synthetic-session',
                        config=Config(retries={'total_max_attempts': 1},
                                      connect_timeout=1, read_timeout=1, **config))


@pytest.fixture(autouse=True)
def forbid_real_sdk_transport(monkeypatch):
    """Fail closed if SDK/moto ever falls through to TCP, DNS or IMDS."""
    def denied(*args, **kwargs):
        raise AssertionError('real_network_forbidden_in_aws_tests')

    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)


@pytest.fixture
def backend():
    with mock_aws():
        client = sdk_client()
        client.create_table(TableName=TABLE, BillingMode='PAY_PER_REQUEST',
                            KeySchema=[{'AttributeName': 'pk', 'KeyType': 'HASH'},
                                       {'AttributeName': 'sk', 'KeyType': 'RANGE'}],
                            AttributeDefinitions=[{'AttributeName': 'pk', 'AttributeType': 'S'},
                                                  {'AttributeName': 'sk', 'AttributeType': 'S'}])
        permissions = {OWNER: True, OTHER: True}
        clients = [client]
        calls = []

        def observe(params, model, **kwargs):
            calls.append((model.name, deepcopy(params)))

        def build(value):
            value.meta.events.register('before-parameter-build.dynamodb', observe)
            return DynamoLeaseStore(client=value, table_name=TABLE,
                                    authorize=lambda owner: permissions.get(owner, False))

        def restart():
            value = sdk_client()
            clients.append(value)
            target.leases = build(value)

        target = SimpleNamespace(leases=build(client), restart=restart, permissions=permissions,
                                 calls=calls, client=client)
        yield target
        for value in clients:
            value.close()


def test_adapter_construction_never_provisions_and_uses_only_indexed_calls(backend):
    assert isinstance(backend.leases, LeaseStore)
    assert backend.calls == []
    original = lease(backend)
    assert backend.leases.is_current(owner_ref=OWNER, lease=original, now=NOW)
    renewed = backend.leases.renew(owner_ref=OWNER, lease=original, now=NOW, ttl=TTL)
    assert backend.leases.release(owner_ref=OWNER, lease=renewed)
    assert {name for name, _ in backend.calls} == {'UpdateItem', 'GetItem'}
    for name, params in backend.calls:
        assert params['Key'] == {'pk': {'S': 'owner#' + OWNER},
                                 'sk': {'S': 'lease#session:shared'}}
        if name == 'GetItem':
            assert params['ConsistentRead'] is True
        else:
            assert params['ConditionExpression']
            assert 'ttl' not in params['ExpressionAttributeNames'].values()


def test_transport_guard_denies_tcp_and_dns_without_attempting_network():
    with socket.socket() as value:
        calls = (lambda: socket.create_connection(('synthetic.invalid', 443)),
                 lambda: socket.getaddrinfo('synthetic.invalid', 443),
                 lambda: value.connect(('192.0.2.1', 443)),
                 lambda: value.connect_ex(('192.0.2.1', 443)))
        for call in calls:
            with pytest.raises(AssertionError, match='real_network_forbidden_in_aws_tests'):
                call()


def test_authorization_is_fresh_for_every_operation_before_sdk_io(backend):
    original = lease(backend)
    backend.permissions[OWNER] = False
    before = len(backend.calls)
    operations = [lambda: lease(backend),
                  lambda: backend.leases.renew(owner_ref=OWNER, lease=original, now=NOW, ttl=TTL),
                  lambda: backend.leases.is_current(owner_ref=OWNER, lease=original, now=NOW),
                  lambda: backend.leases.release(owner_ref=OWNER, lease=original)]
    for operation in operations:
        with pytest.raises(ConditionalConflict, match='lease_owner_not_authorized'):
            operation()
    assert len(backend.calls) == before


def test_microsecond_expiration_is_exact_including_far_future_without_float(backend):
    at = datetime(2400, 1, 1, 0, 0, 0, 123456, tzinfo=timezone.utc)
    original = backend.leases.acquire(owner_ref=OWNER, session_ref='session:shared',
                                      worker_ref='worker:first', now=at, ttl=timedelta(microseconds=1))
    assert original.expires_at == at + timedelta(microseconds=1)
    assert backend.leases.is_current(owner_ref=OWNER, lease=original, now=at)
    assert not backend.leases.is_current(owner_ref=OWNER, lease=original, now=original.expires_at)
    params = backend.calls[0][1]['ExpressionAttributeValues']
    assert int(params[':expiry']['N']) - int(params[':now']['N']) == 1


@pytest.mark.parametrize('at', [datetime(2026, 10, 4),
                               datetime(2026, 10, 4, tzinfo=timezone(timedelta(hours=1)))])
def test_naive_or_non_utc_clock_rejected_before_io(backend, at):
    with pytest.raises(ValueError, match='aware_utc_required'):
        lease(backend, at=at)
    assert not backend.calls


def test_tampered_expiry_or_session_snapshot_cannot_mutate_target(backend):
    original = lease(backend)
    for forged in (replace(original, expires_at=original.expires_at + timedelta(microseconds=1)),
                   replace(original, session_ref='session:other')):
        assert not backend.leases.is_current(owner_ref=OWNER, lease=forged, now=NOW)
        assert backend.leases.renew(owner_ref=OWNER, lease=forged, now=NOW, ttl=TTL) is None
        assert not backend.leases.release(owner_ref=OWNER, lease=forged)
    assert backend.leases.is_current(owner_ref=OWNER, lease=original, now=NOW)
    assert not backend.leases.is_current(owner_ref=OWNER, lease=original,
                                          now=original.expires_at)


def test_two_sdk_clients_contend_and_released_epoch_remains_in_storage(backend):
    original_store = backend.leases
    first = lease(backend)
    backend.restart()
    assert lease(backend, worker='worker:second') is None
    assert original_store.release(owner_ref=OWNER, lease=first)
    assert not original_store.release(owner_ref=OWNER, lease=first)
    second = lease(backend, worker='worker:second')
    assert second.version == first.version + 1
    assert original_store.renew(owner_ref=OWNER, lease=first, now=NOW, ttl=TTL) is None
    assert backend.leases.is_current(owner_ref=OWNER, lease=second, now=NOW)


@pytest.mark.parametrize('operation', ['acquire', 'renew', 'release'])
def test_only_conditional_check_failed_maps_to_contention(backend, operation):
    original = lease(backend)
    function = {'acquire': lambda: lease(backend),
                'renew': lambda: backend.leases.renew(owner_ref=OWNER, lease=original, now=NOW, ttl=TTL),
                'release': lambda: backend.leases.release(owner_ref=OWNER, lease=original)}[operation]
    with Stubber(backend.client) as stub:
        stub.add_client_error('update_item', service_error_code='ConditionalCheckFailedException')
        assert function() is (False if operation == 'release' else None)
        stub.assert_no_pending_responses()
    with Stubber(backend.client) as stub:
        stub.add_client_error('update_item', service_error_code='ProvisionedThroughputExceededException')
        with pytest.raises(ClientError) as caught:
            function()
        assert caught.value.response['Error']['Code'] == 'ProvisionedThroughputExceededException'
        stub.assert_no_pending_responses()


def test_get_errors_propagate_not_false_current(backend):
    original = lease(backend)
    with Stubber(backend.client) as stub:
        stub.add_client_error('get_item', service_error_code='InternalServerError')
        with pytest.raises(ClientError):
            backend.leases.is_current(owner_ref=OWNER, lease=original, now=NOW)
        stub.assert_no_pending_responses()


def test_write_committed_then_timeout_propagates_without_retry(backend, monkeypatch):
    original_update = backend.client.update_item
    attempts = []

    def lost_response(**kwargs):
        attempts.append(True)
        original_update(**kwargs)
        raise ReadTimeoutError(endpoint_url='https://synthetic.invalid')

    monkeypatch.setattr(backend.client, 'update_item', lost_response)
    with pytest.raises(ReadTimeoutError):
        lease(backend)
    assert len(attempts) == 1
    monkeypatch.setattr(backend.client, 'update_item', original_update)
    # The write actually committed in moto: a new acquire is busy, not retried.
    assert lease(backend, worker='worker:second') is None


def test_sdk_default_retry_policy_and_unbounded_timeouts_rejected():
    with mock_aws():
        for config in (Config(), Config(retries={'total_max_attempts': 2}, connect_timeout=1, read_timeout=1),
                       Config(retries={'total_max_attempts': 1}, connect_timeout=11, read_timeout=1)):
            client = boto3.client('dynamodb', region_name='us-east-1',
                                   aws_access_key_id='synthetic-access',
                                   aws_secret_access_key='synthetic-secret', config=config)
            try:
                with pytest.raises(ValueError):
                    DynamoLeaseStore(client=client, table_name=TABLE, authorize=lambda owner: True)
            finally:
                client.close()


def test_raw_numeric_owner_rejected_even_with_allowing_host_policy(backend):
    before = len(backend.calls)
    with pytest.raises(ValueError, match='invalid_lease_reference'):
        backend.leases.acquire(owner_ref='owner:123456789', session_ref='session:shared',
                                worker_ref='worker:first', now=NOW, ttl=TTL)
    assert len(backend.calls) == before
