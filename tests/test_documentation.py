"""Checks for the documentation guardrails only; not commerce product tests."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts/check_docs.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config/radar.example.json").read_text(encoding="utf-8"))

    def test_example_passes(self):
        self.assertEqual(checks.config_errors(self.config), [])

    def test_private_actions_and_paid_fallback_are_rejected(self):
        for section, key in (("ai", "paid_fallback_allowed"), ("security", "outbound_actions_enabled")):
            candidate = deepcopy(self.config)
            candidate[section][key] = True
            self.assertTrue(checks.config_errors(candidate))

    def test_bad_budgets_and_boolean_numbers_are_rejected(self):
        for value in (-1, 0, True, "4"):
            candidate = deepcopy(self.config)
            candidate["concurrency"]["global_workers"] = value
            self.assertTrue(checks.config_errors(candidate))

    def test_duplicate_and_enabled_sources_are_rejected(self):
        candidate = deepcopy(self.config)
        candidate["sources"].append(deepcopy(candidate["sources"][0]))
        self.assertTrue(checks.config_errors(candidate))
        self.config["sources"][0]["enabled"] = True
        self.assertTrue(checks.config_errors(self.config))

    def test_invalid_shape_and_false_economics_are_rejected(self):
        for invalid in ([], {}, {**self.config, "ai": None}):
            self.assertTrue(checks.config_errors(invalid))
        self.config["economics"]["listing_price_is_realized_sale"] = True
        self.assertTrue(checks.config_errors(self.config))

    def test_repository_local_links_exist(self):
        self.assertEqual(checks.link_errors(ROOT), [])

    def test_private_and_dependency_documents_are_not_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for excluded in (".local", "node_modules", ".auth", ".aws-sam"):
                private = root / excluded
                private.mkdir()
                (private / "private.md").write_text("[private](missing.md)", encoding="utf-8")
            self.assertEqual(checks.link_errors(root), [])
            (root / "README.md").write_text("[public](missing.md)", encoding="utf-8")
            self.assertEqual(len(checks.link_errors(root)), 1)


if __name__ == "__main__":
    unittest.main()
