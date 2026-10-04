"""Version selection for canonical envelopes; payload schemas remain B-owned."""
from collections.abc import Mapping

from jsonschema import ValidationError

from radar.contracts import validate


def validate_envelope(envelope):
    if not isinstance(envelope, Mapping):
        raise ValidationError('envelope_object_required')
    if 'schema_version' not in envelope:
        raise ValidationError('invalid_input')
    version = envelope.get('schema_version')
    if type(version) is not int or version not in (1, 2):
        raise ValidationError('unsupported_envelope_version')
    validate(f'envelope.v{version}', envelope)
