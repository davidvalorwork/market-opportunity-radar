"""AST layer guard, with negative fixtures even before domain/application exist.

This checks import dependencies and explicit dynamic loaders, not a security
sandbox or proof that arbitrary Python is free of I/O.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'src' / 'radar'
PURE_STDLIB = {
    '__future__', 'abc', 'collections', 'contextlib', 'copy', 'dataclasses',
    'datetime', 'decimal', 'enum', 'fractions', 'functools', 'hashlib',
    'itertools', 'json', 'math', 'numbers', 'operator', 're', 'statistics',
    'string', 'types', 'typing', 'unicodedata', 'uuid',
}
LAYER_IMPORTS = {
    'domain': {'radar.domain'},
    'ports': {'radar.domain', 'radar.ports'},
    'application': {'radar.domain', 'radar.ports', 'radar.application'},
}


def import_violations(source, *, module, layer, package=False):
    """Return prohibited imports, resolving relative imports and root aliases."""
    tree = ast.parse(source)
    violations = []
    allowed = LAYER_IMPORTS[layer]

    def permitted(name):
        if name.split('.')[0] in PURE_STDLIB:
            return True
        return any(name == prefix or name.startswith(prefix + '.') for prefix in allowed)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ''
            if node.level:
                parts = module.split('.') if package else module.split('.')[:-1]
                if node.level > len(parts):
                    violations.append((node.lineno, '<relative import escapes package>'))
                    continue
                base = '.'.join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
            # "from radar import adapters" must not be allowed through a root.
            names = [base + '.' + alias.name for alias in node.names]
            if permitted(base):
                names = [base]
        elif isinstance(node, ast.Call):
            # The layer intentionally cannot load dependencies dynamically.
            if isinstance(node.func, ast.Name) and node.func.id in {'__import__', 'eval', 'exec'}:
                violations.append((node.lineno, '<dynamic code/import>'))
            continue
        else:
            continue
        for name in names:
            if not permitted(name):
                violations.append((node.lineno, name))
    return violations


def test_actual_layers_follow_dependency_rule():
    files_checked = []
    for layer in LAYER_IMPORTS:
        for path in sorted((ROOT / layer).rglob('*.py')):
            relative = path.relative_to(ROOT.parent).with_suffix('')
            package = relative.name == '__init__'
            module = '.'.join(relative.parts[:-1] if package else relative.parts)
            issues = import_violations(path.read_text(encoding='utf-8'), module=module,
                                       layer=layer, package=package)
            assert not issues, f'{path}: {issues}'
            files_checked.append(path)
    # Real production files must be checked, not only synthetic examples.
    assert ROOT / 'ports' / 'interfaces.py' in files_checked
    assert ROOT / 'ports' / 'types.py' in files_checked


@pytest.mark.parametrize('layer,source', [
    ('domain', 'import boto3'),
    ('domain', 'import socket'),
    ('domain', 'from urllib.request import urlopen'),
    ('domain', 'from radar.ports import Clock'),
    ('domain', 'from radar import adapters'),
    ('domain', 'from radar.adapters.aws import Table'),
    ('domain', 'from ..ports import Clock'),
    ('domain', 'from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n import requests'),
    ('domain', '__import__("requests")'),
    ('domain', 'import importlib as loader\nloader.import_module("boto3")'),
    ('domain', 'exec("import socket")'),
    ('domain', 'from pathlib import Path'),
    ('application', 'import sqlite3'),
    ('application', 'from radar.adapters.local import Repository'),
    ('application', 'from .. import adapters'),
    ('application', 'from radar.contracts import validate'),
    ('application', 'import httpx'),
    ('application', 'from radar import contracts'),
    ('ports', 'from radar.application import run'),
    ('ports', 'from jsonschema import validate'),
])
def test_negative_fixtures_reject_forbidden_dependencies(layer, source):
    assert import_violations(source, module=f'radar.{layer}.core', layer=layer)


@pytest.mark.parametrize('layer,source', [
    ('domain', 'from decimal import Decimal\nfrom dataclasses import dataclass'),
    ('domain', 'from .states import State'),
    ('domain', 'from radar import domain'),
    ('ports', 'from .types import Receipt\nfrom typing import Protocol'),
    ('ports', 'from radar.domain.core import Money'),
    ('application', 'from radar.ports import UnitOfWork'),
    ('application', 'from ..domain.core import Money'),
    ('application', 'from .use_cases import Run'),
    ('application', 'from radar import ports, domain'),
])
def test_positive_fixtures_allow_core_dependencies(layer, source):
    assert not import_violations(source, module=f'radar.{layer}.core', layer=layer)


def test_nested_relative_import_resolution():
    assert not import_violations('from ..core import Money',
                                 module='radar.domain.verticals.products', layer='domain')
    assert import_violations('from ...adapters import Source',
                             module='radar.domain.verticals.products', layer='domain')
    assert not import_violations('from .interfaces import Clock',
                                 module='radar.ports', layer='ports', package=True)
