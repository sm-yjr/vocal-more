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


def _module_name(path: Path, *, package_root: Path = PACKAGE_ROOT) -> str:
    parts = list(path.relative_to(package_root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_relative_import(
    module: str,
    node: ast.ImportFrom,
    *,
    is_package: bool = False,
) -> str | None:
    package_parts = module.split(".") if is_package else module.split(".")[:-1]
    parents_to_drop = node.level - 1
    if parents_to_drop >= len(package_parts):
        return None
    parent_parts = (
        package_parts[:-parents_to_drop]
        if parents_to_drop
        else package_parts
    )
    if node.module:
        parent_parts.extend(node.module.split("."))
    return ".".join(parent_parts) or None


def _is_internal_module(module: str) -> bool:
    return module == "vocal_more" or module.startswith("vocal_more.")


def _is_module_within(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


def _module_paths(
    *,
    source_root: Path,
    package_root: Path,
) -> dict[str, Path]:
    return {
        _module_name(path, package_root=package_root): path
        for path in sorted(source_root.rglob("*.py"))
    }


def _internal_imports_for_path(
    path: Path,
    *,
    package_root: Path,
    known_modules: set[str],
) -> set[str]:
    module = _module_name(path, package_root=package_root)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(
                imported.name
                for imported in node.names
                if _is_internal_module(imported.name)
            )
            continue
        if not isinstance(node, ast.ImportFrom):
            continue

        if node.level:
            imported_module = _resolve_relative_import(
                module,
                node,
                is_package=path.name == "__init__.py",
            )
        else:
            imported_module = node.module
        if not imported_module or not _is_internal_module(imported_module):
            continue

        for imported in node.names:
            candidate = f"{imported_module}.{imported.name}"
            if imported.name != "*" and candidate in known_modules:
                imports.add(candidate)
            else:
                imports.add(imported_module)

    return imports


def _internal_import_graph(
    *,
    source_root: Path = SRC_ROOT,
    package_root: Path = PACKAGE_ROOT,
) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    module_paths = _module_paths(
        source_root=source_root,
        package_root=package_root,
    )
    known_modules = set(module_paths)
    for module, path in module_paths.items():
        graph[module].update(
            _internal_imports_for_path(
                path,
                package_root=package_root,
                known_modules=known_modules,
            )
        )
        graph.setdefault(module, set())
    return graph


def _forbidden_import_violations(
    *,
    source_root: Path = SRC_ROOT,
    package_root: Path = PACKAGE_ROOT,
) -> list[str]:
    module_paths = _module_paths(
        source_root=source_root,
        package_root=package_root,
    )
    known_modules = set(module_paths)
    violations: list[str] = []

    for module, path in module_paths.items():
        imported_modules = _internal_imports_for_path(
            path,
            package_root=package_root,
            known_modules=known_modules,
        )
        for prefix, forbidden_prefixes in FORBIDDEN_IMPORTS.items():
            if not _is_module_within(module, prefix):
                continue
            for imported in imported_modules:
                if any(
                    _is_module_within(imported, forbidden)
                    for forbidden in forbidden_prefixes
                ):
                    violations.append(f"{module} -> {imported}")

    return sorted(violations)


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
    assert not _forbidden_import_violations()


def test_runtime_facade_uses_mode_runtime_port_instead_of_mode_internals() -> None:
    path = SRC_ROOT / "application" / "runtime_facade.py"
    imports = _internal_imports_for_path(
        path,
        package_root=PACKAGE_ROOT,
        known_modules=set(
            _module_paths(source_root=SRC_ROOT, package_root=PACKAGE_ROOT)
        ),
    )
    forbidden = (
        "vocal_more.core",
        "vocal_more.infrastructure",
        "vocal_more.modes",
        "vocal_more.ui",
    )
    assert not [
        imported
        for imported in imports
        if any(_is_module_within(imported, prefix) for prefix in forbidden)
    ]

    source = path.read_text(encoding="utf-8")
    assert '"_recorder"' not in source
    assert '"_asr"' not in source


def test_recording_retry_adapters_do_not_implement_provider_or_billing_work() -> None:
    for relative_path in ("rpc_handler.py", "ui/settings_window.py"):
        source = (SRC_ROOT / relative_path).read_text(encoding="utf-8")
        assert "core.asr_engine" not in source
        assert "RETRY_ASR_MODEL" not in source
        assert "merge_billing" not in source


def test_adapter_background_workloads_use_dedicated_executors() -> None:
    settings_source = (SRC_ROOT / "ui" / "settings_window.py").read_text(
        encoding="utf-8"
    )
    rpc_source = (SRC_ROOT / "rpc_handler.py").read_text(encoding="utf-8")

    assert "_background_tasks" not in settings_source
    assert "_model_check_tasks" in settings_source
    assert "_recording_maintenance_tasks" in settings_source
    assert "_meeting_tasks" in settings_source
    assert "_background_tasks" not in rpc_source
    assert "_meeting_tasks" in rpc_source


def test_plain_import_is_checked_for_upward_dependency(tmp_path: Path) -> None:
    package_root = tmp_path / "src"
    source_root = package_root / "vocal_more"
    domain_path = source_root / "domain" / "plain_import.py"
    domain_path.parent.mkdir(parents=True)
    domain_path.write_text(
        "import vocal_more.infrastructure.config_repository\n",
        encoding="utf-8",
    )

    violations = _forbidden_import_violations(
        source_root=source_root,
        package_root=package_root,
    )

    assert violations == [
        "vocal_more.domain.plain_import -> "
        "vocal_more.infrastructure.config_repository"
    ]


def test_init_module_names_are_canonicalized(tmp_path: Path) -> None:
    package_root = tmp_path / "src"
    init_path = package_root / "vocal_more" / "domain" / "__init__.py"
    init_path.parent.mkdir(parents=True)
    init_path.write_text("", encoding="utf-8")

    assert _module_name(init_path, package_root=package_root) == "vocal_more.domain"


def test_from_dot_import_sibling_cycle_is_detected(tmp_path: Path) -> None:
    package_root = tmp_path / "src"
    source_root = package_root / "vocal_more"
    package_path = source_root / "feature"
    package_path.mkdir(parents=True)
    (package_path / "__init__.py").write_text("", encoding="utf-8")
    (package_path / "first.py").write_text(
        "from . import second\n",
        encoding="utf-8",
    )
    (package_path / "second.py").write_text(
        "from . import first\n",
        encoding="utf-8",
    )

    graph = _internal_import_graph(
        source_root=source_root,
        package_root=package_root,
    )
    cycles = {
        frozenset(component)
        for component in _strongly_connected_components(graph)
        if len(component) > 1
    }

    assert cycles == {
        frozenset(
            {
                "vocal_more.feature.first",
                "vocal_more.feature.second",
            }
        )
    }
