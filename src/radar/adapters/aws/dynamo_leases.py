"""Owner-scoped DynamoDB leases. No provisioning, login, retry loop or TTL deletion."""
from datetime import datetime, timedelta, timezone
from functools import cache
import re
from typing import Callable
from uuid import uuid4

from jsonschema import Draft202012Validator

from radar.contracts import load_schema
from radar.ports.types import ConditionalConflict, Lease


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@cache
def _ref_validator(definition):
    return Draft202012Validator(load_schema('common.v1')['$defs'][definition])


def _ref(value, definition):
    if not _ref_validator(definition).is_valid(value):
        raise ValueError('invalid_lease_reference')


def _micros(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError('aware_utc_required')
    delta = value - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _expiry(now, ttl):
    _micros(now)
    if not isinstance(ttl, timedelta) or ttl <= timedelta(0):
        raise ValueError('positive_ttl_required')
    return now + ttl


class DynamoLeaseStore:
    """LeaseStore using a preconfigured low-level boto3 DynamoDB client.

    authorize is a trusted host policy, evaluated before each operation; an
    owner string is not proof of authority. Automatic SDK retries are forbidden
    here because a timed-out write may already have committed. Such errors
    propagate for reconciliation; they are never presented as lease contention.
    """

    def __init__(self, *, client, table_name: str, authorize: Callable[[str], bool]):
        from botocore.exceptions import ClientError

        if not isinstance(table_name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{3,255}', table_name):
            raise ValueError('invalid_lease_table')
        if not callable(authorize):
            raise ValueError('lease_authorizer_required')
        config = client.meta.config
        if config.retries.get('total_max_attempts') != 1:
            raise ValueError('lease_sdk_retries_must_be_disabled')
        for timeout in (config.connect_timeout, config.read_timeout):
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 10:
                raise ValueError('bounded_lease_sdk_timeout_required')
        self.client, self.table_name, self.authorize = client, table_name, authorize
        self._client_error = ClientError

    def _key(self, owner_ref, session_ref):
        _ref(owner_ref, 'opaque_ref')
        _ref(session_ref, 'session_ref')
        if self.authorize(owner_ref) is not True:
            raise ConditionalConflict('lease_owner_not_authorized')
        return {'pk': {'S': 'owner#' + owner_ref}, 'sk': {'S': 'lease#' + session_ref}}

    @staticmethod
    def _decode(item):
        return Lease(item['session_ref']['S'], item['worker_ref']['S'], item['token']['S'],
                     EPOCH + timedelta(microseconds=int(item['expires_us']['N'])),
                     int(item['version']['N']))

    @staticmethod
    def _binding(lease):
        if not isinstance(lease, Lease):
            raise ValueError('lease_snapshot_required')
        _ref(lease.worker_ref, 'opaque_ref')
        if not isinstance(lease.token, str) or not 1 <= len(lease.token) <= 128:
            raise ValueError('invalid_lease_token')
        if type(lease.version) is not int or lease.version < 1:
            raise ValueError('invalid_lease_version')
        return {':worker': {'S': lease.worker_ref}, ':token': {'S': lease.token},
                ':version': {'N': str(lease.version)},
                ':old_expiry': {'N': str(_micros(lease.expires_at))}}

    def _update(self, *, key, expression, condition, names, values):
        try:
            return self.client.update_item(
                TableName=self.table_name, Key=key, UpdateExpression=expression,
                ConditionExpression=condition, ExpressionAttributeNames=names,
                ExpressionAttributeValues=values, ReturnValues='ALL_NEW')['Attributes']
        except self._client_error as error:
            if error.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
                return None
            raise

    def acquire(self, *, owner_ref, session_ref, worker_ref, now, ttl):
        expires = _expiry(now, ttl)
        _ref(worker_ref, 'opaque_ref')
        key = self._key(owner_ref, session_ref)
        item = self._update(
            key=key,
            expression='SET #owner=:owner, #session=:session, #worker=:worker, '
                       '#token=:token, #expiry=:expiry, #version=if_not_exists(#version,:zero)+:one',
            condition='attribute_not_exists(#pk) OR #expiry <= :now',
            names={'#pk': 'pk', '#owner': 'owner_ref', '#session': 'session_ref',
                   '#worker': 'worker_ref', '#token': 'token', '#expiry': 'expires_us', '#version': 'version'},
            values={':owner': {'S': owner_ref}, ':session': {'S': session_ref},
                    ':worker': {'S': worker_ref}, ':token': {'S': uuid4().hex},
                    ':expiry': {'N': str(_micros(expires))}, ':now': {'N': str(_micros(now))},
                    ':zero': {'N': '0'}, ':one': {'N': '1'}})
        return None if item is None else self._decode(item)

    def renew(self, *, owner_ref, lease, now, ttl):
        expires = _expiry(now, ttl)
        values = self._binding(lease)
        key = self._key(owner_ref, lease.session_ref)
        values.update({':now': {'N': str(_micros(now))}, ':expiry': {'N': str(_micros(expires))},
                       ':one': {'N': '1'}})
        item = self._update(
            key=key, expression='SET #expiry=:expiry, #version=#version+:one',
            condition='#worker=:worker AND #token=:token AND #version=:version '
                      'AND #expiry=:old_expiry AND #expiry > :now',
            names={'#worker': 'worker_ref', '#token': 'token', '#version': 'version', '#expiry': 'expires_us'},
            values=values)
        return None if item is None else self._decode(item)

    def is_current(self, *, owner_ref, lease, now):
        self._binding(lease)
        current_time = _micros(now)
        key = self._key(owner_ref, lease.session_ref)
        item = self.client.get_item(TableName=self.table_name, Key=key,
                                    ConsistentRead=True).get('Item')
        return bool(item and self._decode(item) == lease and _micros(lease.expires_at) > current_time)

    def release(self, *, owner_ref, lease):
        values = self._binding(lease)
        key = self._key(owner_ref, lease.session_ref)
        # A persistent tombstone retains the epoch; never DeleteItem or TTL this row.
        values[':released'] = {'N': str(_micros(datetime.min.replace(tzinfo=timezone.utc)))}
        item = self._update(
            key=key, expression='SET #expiry=:released',
            condition='#worker=:worker AND #token=:token AND #version=:version AND #expiry=:old_expiry',
            names={'#worker': 'worker_ref', '#token': 'token', '#version': 'version', '#expiry': 'expires_us'},
            values=values)
        return item is not None
