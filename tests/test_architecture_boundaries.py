from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "vocal_more"
PACKAGE_ROOT = SRC_ROOT.parent
EXPECTED_LAYER_PACKAGES = (
    "application",
    "domain",
    "infrastructure",
    "interfaces",
)
FORBIDDEN_IMPORTS = {
    "vocal_more.domain": (
        "vocal_more.ui",
        "vocal_more.interfaces",
        "vocal_more.infrastructure",
    ),
    "vocal_more.application": (
        "vocal_more.ui",
        "vocal_more.interfaces",
    ),
}


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(PACKAGE_ROOT).with_suffix("").parts)


def _resolve_relative_import(module: str, node: ast.ImportFrom) -> str | None:
    base_parts = module.split(".")
    if node.level > len(base_parts):
        return None
    parent_parts = base_parts[:-node.level]
    if node.module:
        parent_parts.extend(node.module.split("."))
    return ".".join(parent_parts) or None


def _internal_import_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for path in SRC_ROOT.rglob("*.py"):
        module = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for imported in node.names:
                    if imported.name.startswith("vocal_more"):
                        graph[module].add(imported.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    resolved = _resolve_relative_import(module, node)
                    if resolved and resolved.startswith("vocal_more"):
                        graph[module].add(resolved)
                elif node.module and node.module.startswith("vocal_more"):
                    graph[module].add(node.module)
        graph.setdefault(module, set())
    return graph


def _strongly_connected_components(
    graph: dict[str, set[str]],
) -> list[list[str]]:
    index = 0
    stack: list[str] = []
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def strongconnect(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for neighbor in graph[node]:
            if neighbor not in graph:
                continue
            if neighbor not in indices:
                strongconnect(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[neighbor])

        if lowlinks[node] != indices[node]:
            return

        component: list[str] = []
        while stack:
            current = stack.pop()
            on_stack.remove(current)
            component.append(current)
            if current == node:
                break
        components.append(component)

    for node in sorted(graph):
        if node not in indices:
            strongconnect(node)

    return components


def test_refactor_layer_packages_exist() -> None:
    missing = [
        name
        for name in EXPECTED_LAYER_PACKAGES
        if not (SRC_ROOT / name / "__init__.py").exists()
    ]
    assert not missing, missing


def test_internal_modules_have_no_cycles() -> None:
    components = _strongly_connected_components(_internal_import_graph())
    cycles = sorted(
        [sorted(component) for component in components if len(component) > 1]
    )
    assert not cycles, cycles


def test_domain_and_application_do_not_import_upward() -> None:
    violations: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        module = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            imported = None
            if node.level:
                imported = _resolve_relative_import(module, node)
            elif node.module:
                imported = node.module
            if not imported:
                continue
            for prefix, forbidden_prefixes in FORBIDDEN_IMPORTS.items():
                if module.startswith(prefix) and imported.startswith(forbidden_prefixes):
                    violations.append(f"{module} -> {imported}")
    assert not violations, violations
