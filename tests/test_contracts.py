"""Golden examples for contracts/; the same files are checked by the Node and Go validators."""

from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
if importlib.util.find_spec("radar") is None:  # plain checkout; an installed radar wins
    sys.path.insert(0, str(ROOT / "src"))
if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "hypothesis")):
    raise unittest.SkipTest("install '.[test]' (requirements.lock) to run contract tests")

from hypothesis import given, strategies as st  # noqa: E402
from jsonschema import Draft202012Validator, ValidationError  # noqa: E402

from radar import contracts  # noqa: E402

EXAMPLES = ROOT / "contracts" / "examples"
SCHEMAS = contracts.schema_names()
PRIVATE_FIELDS = {"declared_phone", "text", "messages"}


def examples(group, schema):
    return sorted((EXAMPLES / group / schema).glob("*.json"))


class ContractTests(unittest.TestCase):
    def test_schemas_are_valid_2020_12_with_closed_roots(self):
        for name in SCHEMAS:
            schema = contracts.load_schema(name)
            Draft202012Validator.check_schema(schema)
            self.assertTrue(schema["$id"].endswith(f"/contracts/{name}.json"))
            if name != "common.v1":
                self.assertIs(schema["additionalProperties"], False, name)
                self.assertEqual(schema["properties"]["schema_version"], {"$ref": "common.v1.json#/$defs/schema_version"})

    def test_transport_schemas_declare_no_private_fields(self):
        def keys(node):
            if isinstance(node, dict):
                yield from node.get("properties", {})
                for value in node.values():
                    yield from keys(value)
            elif isinstance(node, list):
                for value in node:
                    yield from keys(value)
        for name in SCHEMAS:
            if ".private." not in name:
                self.assertFalse(PRIVATE_FIELDS & set(keys(contracts.load_schema(name))), name)

    def test_every_schema_has_golden_examples(self):
        for name in SCHEMAS:
            if name == "common.v1":
                continue
            self.assertGreaterEqual(len(examples("valid", name)), 2, name)
            self.assertGreaterEqual(len(examples("invalid", name)), 3, name)
        folders = {path.name for group in ("valid", "invalid") for path in (EXAMPLES / group).iterdir()}
        self.assertEqual(folders, set(SCHEMAS) - {"common.v1"})

    def test_valid_examples_pass(self):
        for name in SCHEMAS:
            for path in examples("valid", name):
                with self.subTest(example=f"{name}/{path.name}"):
                    contracts.validate(name, json.loads(path.read_text(encoding="utf-8")))

    def test_invalid_examples_fail(self):
        for name in SCHEMAS:
            for path in examples("invalid", name):
                with self.subTest(example=f"{name}/{path.name}"), self.assertRaises(ValidationError):
                    contracts.validate(name, json.loads(path.read_text(encoding="utf-8")))

    def test_capability_registry_is_valid_and_not_overclaimed(self):
        registry = json.loads((ROOT / "contracts" / "capabilities.json").read_text(encoding="utf-8"))
        contracts.validate("capabilities.v1", registry)
        statuses = {entry["status"] for backends in registry["platforms"].values()
                    for operations in backends.values() for entry in operations.values()}
        self.assertNotIn("probado_real", statuses)

    def test_oversize_envelope_fails_only_by_size(self):
        instance = json.loads((EXAMPLES / "invalid/envelope.v1/oversize.json").read_text(encoding="utf-8"))
        self.assertEqual(list(contracts._validator("envelope.v1").iter_errors(instance)), [])
        with self.assertRaisesRegex(ValidationError, "exceeds 32768 bytes"):
            contracts.validate("envelope.v1", instance)

    @given(st.decimals(min_value=Decimal("-1e12"), max_value=Decimal("1e12"), places=2,
                       allow_nan=False, allow_infinity=False))
    def test_money_is_decimal_string_never_float(self, amount):
        money = {"$ref": "https://market-opportunity-radar.invalid/contracts/common.v1.json#/$defs/money"}
        validator = Draft202012Validator(money, registry=contracts.registry())
        text = format(amount, "f")
        self.assertTrue(validator.is_valid({"amount": text, "currency": "USD"}), text)
        self.assertFalse(validator.is_valid({"amount": float(amount), "currency": "USD"}))


if __name__ == "__main__":
    unittest.main()
