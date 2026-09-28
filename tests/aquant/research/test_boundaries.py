"""Independent source and runtime guards for the deliberately pure Phase 3A."""

import ast
import builtins
import io
import os
from pathlib import Path
import socket
import subprocess

import pytest

from aquant.markets.china import ChinaEquityRuleBook
from aquant.research import (
    ResearchConfig, ResearchRun, UniverseDefinition, UniverseSnapshot,
    validate_research_inputs,
)


PACKAGE = Path(__file__).resolve().parents[3] / "aquant" / "research"
ALLOWED_IMPORTS = {
    "datetime", "decimal", "enum", "fractions", "hashlib", "json", "typing", "pydantic",
    "aquant.domain", "aquant.markets.china", "aquant.data.versioning",
}
FORBIDDEN_NAMES = {
    "TargetPosition", "ManualOrderPlan", "ManualExecution", "StrategyRun",
    "SnapshotReader", "SnapshotPublisher", "MarketDataSet", "compute_data_version",
    "sha256_file", "open", "exec", "eval", "__import__", "import_module",
}
FORBIDDEN_CALLS = {
    "open", "read_text", "read_bytes", "write_text", "write_bytes", "mkdir",
    "unlink", "rename", "replace", "glob", "rglob", "connect", "urlopen",
    "read_csv", "read_parquet", "to_parquet", "getenv", "getenvb",
}


def boundary_errors(source):
    """Closed import surface, including aliases/dynamic imports; no file calls."""
    errors = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                imports = [item.name for item in node.names]
            elif node.level:
                imports = []
                if node.level != 1 or node.module not in {"models", "universe"}:
                    errors.append("unapproved relative import")
            else:
                imports = [node.module]
            errors.extend(f"unapproved import: {name}" for name in imports if name not in ALLOWED_IMPORTS)
            if isinstance(node, ast.ImportFrom) and node.module == "aquant.data.versioning":
                if {item.name for item in node.names} != {"canonical_json_bytes"}:
                    errors.append("only public canonical_json_bytes is permitted")
            if any(item.name in FORBIDDEN_NAMES or item.name == "*" for item in node.names):
                errors.append("forbidden imported name")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            errors.append(f"forbidden name: {node.id}")
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if name in FORBIDDEN_CALLS:
                errors.append(f"forbidden IO call: {name}")
    return errors


def test_research_static_dependency_and_io_boundary():
    sources = sorted(PACKAGE.glob("*.py"))
    assert {path.name for path in sources} == {"__init__.py", "models.py", "universe.py"}
    for path in sources:
        assert not boundary_errors(path.read_text(encoding="utf-8")), path


@pytest.mark.parametrize("source", [
    "import akshare as data", "from aquant.data.providers import AkShareChinaDataProvider",
    "from aquant.data.storage import DuckDBHistoryReader", "from aquant.data.snapshots import SnapshotReader",
    "from ashare.data import get_history", "from api.services import Service",
    "from aquant.domain import TargetPosition as Output", "from aquant.domain import ManualOrderPlan",
    "from aquant.data.versioning import sha256_file", "import os; os.getenv('KEY')",
    "import importlib; importlib.import_module('akshare')", "__import__('socket')",
    "open('data.csv')", "p.read_text()", "eval('1')", "from aquant.domain import *",
])
def test_guard_detects_forbidden_examples_independently(source):
    assert boundary_errors(source)


def test_model_lifecycle_performs_no_active_io(monkeypatch, definition_values, config_values, universe, run_values):
    # Windows loads the stdlib timezone resource on first use, not market data.
    # Resolve it before the active-IO guard; do not stub the actual rulebook.
    market_timezone = ChinaEquityRuleBook().timezone
    assert market_timezone is not None

    def forbidden(*args, **kwargs):
        raise AssertionError("research contracts must not perform active IO")

    with monkeypatch.context() as guard:
        for target, name in [
            (builtins, "open"), (io, "open"), (os, "open"), (os, "getenv"),
            (Path, "read_text"), (Path, "read_bytes"), (Path, "write_text"), (Path, "write_bytes"),
            (subprocess, "run"), (subprocess, "Popen"),
            (socket, "getaddrinfo"), (socket, "create_connection"), (socket.socket, "connect"),
        ]:
            guard.setattr(target, name, forbidden)
        definition = UniverseDefinition.create(**definition_values)
        frozen = UniverseSnapshot.from_definition(definition, known_at=universe.known_at, membership_basis=universe.membership_basis)
        config = ResearchConfig.create(**config_values)
        validate_research_inputs(config, frozen)
        for model in (definition, frozen, config, ResearchRun(**run_values)):
            assert type(model).model_validate_json(model.model_dump_json()) == model.verify()


def test_parent_network_guard_is_active():
    with pytest.raises(AssertionError, match="must not access the network"):
        socket.create_connection(("unreachable.invalid", 80))
