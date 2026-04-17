"""Compatibility helpers for loading old YAML files safely."""

from __future__ import annotations

from typing import Any

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode


class CompatSafeLoader(yaml.SafeLoader):
    """SafeLoader with fallbacks for legacy PyYAML python/* tags."""


def _construct_node_value(loader: CompatSafeLoader, node):
    if isinstance(node, ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, MappingNode):
        return loader.construct_mapping(node)
    raise yaml.constructor.ConstructorError(
        None,
        None,
        f"unsupported YAML node type: {type(node).__name__}",
        node.start_mark,
    )


def _coerce_python_tag_value(suffix: str, value: Any) -> Any:
    if suffix.endswith("builtins.str"):
        if isinstance(value, list):
            if len(value) == 1:
                return str(value[0])
            return "".join(str(item) for item in value)
        return str(value)
    if suffix.endswith("builtins.int"):
        if isinstance(value, list) and value:
            value = value[0]
        return int(value)
    if suffix.endswith("builtins.float"):
        if isinstance(value, list) and value:
            value = value[0]
        return float(value)
    if suffix.endswith("builtins.bool"):
        if isinstance(value, list) and value:
            value = value[0]
        return bool(value)
    if suffix.endswith("tuple") and isinstance(value, list):
        return list(value)
    return value


def _construct_python_tag(loader: CompatSafeLoader, suffix: str, node):
    value = _construct_node_value(loader, node)
    return _coerce_python_tag_value(suffix, value)


CompatSafeLoader.add_multi_constructor(
    "tag:yaml.org,2002:python/",
    _construct_python_tag,
)


def safe_load_compat(stream_or_text: Any) -> Any:
    """Load YAML while tolerating legacy python/* tags as plain data."""
    return yaml.load(stream_or_text, Loader=CompatSafeLoader)
