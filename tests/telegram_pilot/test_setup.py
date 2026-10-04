import json

import pytest

from radar.entrypoints.telegram_pilot import _allowlist_bytes


def test_operator_annotation_does_not_break_strict_enrollment(tmp_path):
    path = tmp_path/'allowlist.json'
    path.write_text(json.dumps({'schema_version':1,'entries':[], 'note':'operator annotation'}))
    assert json.loads(_allowlist_bytes(path)) == {'schema_version':1,'entries':[]}


def test_operator_unknown_authority_field_is_not_normalized(tmp_path):
    path = tmp_path/'allowlist.json'
    path.write_text(json.dumps({'schema_version':1,'entries':[], 'allow_everyone':True}))
    with pytest.raises(ValueError,match='pilot_allowlist_invalid'):
        _allowlist_bytes(path)
