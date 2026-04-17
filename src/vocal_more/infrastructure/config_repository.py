"""YAML-backed repository for persisted app config."""

from __future__ import annotations

from pathlib import Path

import yaml

from ..domain.config_models import AppConfig
from ..paths import default_app_paths
from ..yaml_compat import safe_load_compat
from .compatibility_repair import backup_yaml_file, repair_config_file


class ConfigRepository:
    """Load and save app configuration from YAML."""

    def __init__(
        self,
        *,
        base_dir: Path | None = None,
        config_path: Path | None = None,
        config_cls: type[AppConfig] = AppConfig,
    ) -> None:
        self.paths = default_app_paths(base_dir)
        self.config_path = Path(config_path) if config_path is not None else self.paths.config_path
        self._config_cls = config_cls

    def load(self) -> AppConfig:
        config = self._config_cls()
        if not self.config_path.exists():
            return config

        repair_config_file(self.config_path, config_cls=self._config_cls)
        try:
            with open(self.config_path, encoding="utf-8") as f:
                data = safe_load_compat(f) or {}
        except Exception:
            backup_yaml_file(self.config_path, "config-load-failed")
            return config

        return self._config_cls._from_dict(data)

    def save(self, config: AppConfig) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(
                config.to_dict(),
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
