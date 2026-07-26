"""Privacy-bound application-context personalization."""

from __future__ import annotations

from collections.abc import Callable

from ..domain.app_context import (
    AppContext,
    classify_app_context,
    instruction_for_context,
)


SENSITIVE_BUNDLE_IDS = {
    "com.1password.1password",
    "com.agilebits.onepassword7",
    "com.apple.keychainaccess",
    "com.apple.passwords",
    "com.bitwarden.desktop",
    "com.lastpass.lastpass",
}


class ContextPersonalizationService:
    """Convert transient app identity into an abstract, aggregate-only profile."""

    def __init__(
        self,
        *,
        config,
        app_provider: Callable[[], str],
        repository,
    ) -> None:
        self.config = config
        self._app_provider = app_provider
        self._repository = repository

    def capture(self) -> AppContext | None:
        if not self.config.enabled:
            return None
        try:
            bundle_id = str(self._app_provider() or "").strip()
        except Exception as exc:
            print(f"[ContextPersonalization] App context capture failed: {exc}")
            return None
        if not bundle_id:
            return None
        normalized = bundle_id.lower()
        excluded = {
            str(item).strip().lower()
            for item in self.config.excluded_bundle_ids
            if str(item).strip()
        }
        if normalized in {item.lower() for item in SENSITIVE_BUNDLE_IDS}:
            return None
        if normalized in excluded:
            return None
        try:
            return classify_app_context(bundle_id)
        except Exception as exc:
            print(f"[ContextPersonalization] App context classification failed: {exc}")
            return None

    @staticmethod
    def instruction(context: AppContext | None) -> str:
        return instruction_for_context(context)

    def record_success(self, context: AppContext | None) -> None:
        if context is None or self._repository is None:
            return
        try:
            self._repository.increment(context)
        except Exception as exc:
            print(f"[ContextPersonalization] Profile update failed: {exc}")

    def summary(self) -> dict:
        if self._repository is None:
            return {
                "counts": {
                    "development": 0,
                    "general": 0,
                    "messaging": 0,
                    "writing": 0,
                },
                "total": 0,
            }
        return self._repository.summary()

    def reset(self) -> None:
        if self._repository is not None:
            self._repository.reset()


def build_context_personalization_service(*, config, paths=None):
    from ..infrastructure.context_profile_repository import ContextProfileRepository
    from ..infrastructure.macos_app_context import frontmost_bundle_id
    from ..paths import default_app_paths

    app_paths = paths or default_app_paths()
    return ContextPersonalizationService(
        config=config.context_personalization,
        app_provider=frontmost_bundle_id,
        repository=ContextProfileRepository(app_paths.context_profile_path),
    )


__all__ = [
    "ContextPersonalizationService",
    "SENSITIVE_BUNDLE_IDS",
    "build_context_personalization_service",
]
