"""Boundary test: enforces the import rules in AGENTS.md §4.

Owner: Winston
Diagram box: none; proves the trust boundary on the architecture slide
Contract: none
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AI_SIDE = ("app.intent", "app.validation")
BANK_SIDE = ("app.bank",)
FORBIDDEN_FOR_BANK = ("app.intent", "app.validation", "app.llm")
LEDGER_SCAN = ("app", "web", "scripts")  # tests may import the ledger to test it


def _module_of(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def _imports(source: str, module: str) -> set[str]:
    """Return every module name a file imports, with relative imports made absolute.

    `from app.bank import ledger` yields `app.bank.ledger`, so importing a submodule
    through its package is caught the same way as importing it directly.
    """
    found = set()
    package = module.rsplit(".", 1)[0]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parent = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                base = f"{parent}.{base}" if base else parent
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def _is_under(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _in(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(_is_under(name, prefix) for prefix in prefixes)


def _violations(source: str, module: str) -> list[str]:
    """List the AGENTS §4 import rules a file breaks."""
    problems = []
    for name in _imports(source, module):
        if _in(module, AI_SIDE) and _is_under(name, "app.bank"):
            if not _is_under(name, "app.bank.readonly"):
                problems.append(f"{module} imports {name}; the AI side may only use readonly")
        if _in(module, BANK_SIDE) and _in(name, FORBIDDEN_FOR_BANK):
            problems.append(f"{module} imports {name}; the bank must not call the AI side")
        if _is_under(name, "app.bank.ledger") and module != "app.bank.gateway":
            if _in(module, LEDGER_SCAN):
                problems.append(f"{module} imports the ledger; only app.bank.gateway may")
    return problems


def test_repo_follows_import_rules() -> None:
    problems = []
    for path in sorted(ROOT.glob("**/*.py")):
        if ".venv" in path.parts or "node_modules" in path.parts:
            continue
        problems += _violations(path.read_text("utf-8"), _module_of(path))
    assert problems == []


def test_checker_catches_ledger_import_in_intent() -> None:
    assert _violations("from app.bank import ledger\n", "app.intent.gate")


def test_checker_catches_other_bank_imports_from_ai_side() -> None:
    assert _violations("import app.bank.gateway\n", "app.validation.validator")
    assert _violations("from app import bank\n", "app.intent.options")
    assert _violations("from ..bank import rules\n", "app.intent.options")


def test_checker_allows_readonly_from_ai_side() -> None:
    assert not _violations("from app.bank import readonly\n", "app.intent.options")
    assert not _violations("from app.bank.readonly import get_accounts\n", "app.intent.draft")


def test_checker_catches_bank_calling_ai_side() -> None:
    assert _violations("from app.llm import complete\n", "app.bank.rules")
    assert _violations("from ..intent import draft\n", "app.bank.gateway")


def test_checker_allows_only_gateway_to_import_ledger() -> None:
    assert not _violations("from . import ledger\n", "app.bank.gateway")
    assert _violations("from . import ledger\n", "app.bank.router")
    assert _violations("from app.bank import ledger\n", "web.jev")
