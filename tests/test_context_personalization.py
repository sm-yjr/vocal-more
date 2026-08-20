import json


def test_classifies_only_coarse_app_categories():
    from vocal_more.domain.app_context import classify_app_context

    assert classify_app_context("com.microsoft.VSCode").category == "development"
    assert classify_app_context("com.tinyspeck.slackmacgap").category == "messaging"
    assert classify_app_context("com.apple.Notes").category == "writing"
    assert classify_app_context("com.apple.Safari").category == "general"


def test_context_instruction_contains_no_app_identity():
    from vocal_more.domain.app_context import (
        AppContext,
        instruction_for_context,
    )

    context = AppContext(
        category="development",
        bundle_id="com.microsoft.VSCode",
    )

    instruction = instruction_for_context(context)

    assert "代码、命令、API 名、路径和英文标识符" in instruction
    assert "VSCode" not in instruction
    assert "com.microsoft" not in instruction


def test_sensitive_apps_are_never_contextualized():
    from vocal_more.application.context_personalization import (
        ContextPersonalizationService,
    )
    from vocal_more.domain.config_models import ContextPersonalizationConfig

    service = ContextPersonalizationService(
        config=ContextPersonalizationConfig(enabled=True),
        app_provider=lambda: "com.1password.1password",
        repository=None,
    )

    assert service.capture() is None


def test_macos_passwords_app_is_never_contextualized():
    from vocal_more.application.context_personalization import (
        ContextPersonalizationService,
    )
    from vocal_more.domain.config_models import ContextPersonalizationConfig

    service = ContextPersonalizationService(
        config=ContextPersonalizationConfig(enabled=True),
        app_provider=lambda: "com.apple.Passwords",
        repository=None,
    )

    assert service.capture() is None


def test_excluded_apps_are_never_contextualized():
    from vocal_more.application.context_personalization import (
        ContextPersonalizationService,
    )
    from vocal_more.domain.config_models import ContextPersonalizationConfig

    service = ContextPersonalizationService(
        config=ContextPersonalizationConfig(
            enabled=True,
            excluded_bundle_ids=["com.example.private"],
        ),
        app_provider=lambda: "com.example.private",
        repository=None,
    )

    assert service.capture() is None


def test_profile_repository_persists_only_aggregate_category_counts(tmp_path):
    from vocal_more.domain.app_context import AppContext
    from vocal_more.infrastructure.context_profile_repository import (
        ContextProfileRepository,
    )

    path = tmp_path / "context-profile.json"
    repository = ContextProfileRepository(path)
    repository.increment(
        AppContext(
            category="messaging",
            bundle_id="com.tinyspeck.slackmacgap",
        )
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    serialized = path.read_text(encoding="utf-8")

    assert payload == {
        "schema_version": 1,
        "category_counts": {
            "development": 0,
            "general": 0,
                "messaging": 1,
                "terminal": 0,
                "writing": 0,
        },
    }
    assert "slack" not in serialized.lower()
    assert "bundle" not in serialized.lower()


def test_profile_repository_recovers_from_malformed_data_and_resets(tmp_path):
    from vocal_more.domain.app_context import AppContext
    from vocal_more.infrastructure.context_profile_repository import (
        ContextProfileRepository,
    )

    path = tmp_path / "context-profile.json"
    path.write_text("{bad json", encoding="utf-8")
    repository = ContextProfileRepository(path)

    assert repository.summary()["total"] == 0

    repository.increment(AppContext(category="writing", bundle_id="com.apple.Notes"))
    assert repository.summary()["counts"]["writing"] == 1

    repository.reset()
    assert repository.summary() == {
        "counts": {
            "development": 0,
            "general": 0,
                "messaging": 0,
                "terminal": 0,
                "writing": 0,
        },
        "total": 0,
    }


def test_service_records_only_successful_context_category(tmp_path):
    from vocal_more.application.context_personalization import (
        ContextPersonalizationService,
    )
    from vocal_more.domain.config_models import ContextPersonalizationConfig
    from vocal_more.infrastructure.context_profile_repository import (
        ContextProfileRepository,
    )

    repository = ContextProfileRepository(tmp_path / "context-profile.json")
    service = ContextPersonalizationService(
        config=ContextPersonalizationConfig(enabled=True),
        app_provider=lambda: "com.microsoft.VSCode",
        repository=repository,
    )

    context = service.capture()
    service.record_success(context)

    assert context is not None
    assert context.category == "development"
    assert service.summary()["counts"]["development"] == 1


def test_disabled_service_does_not_capture_or_persist(tmp_path):
    from vocal_more.application.context_personalization import (
        ContextPersonalizationService,
    )
    from vocal_more.domain.config_models import ContextPersonalizationConfig
    from vocal_more.infrastructure.context_profile_repository import (
        ContextProfileRepository,
    )

    repository = ContextProfileRepository(tmp_path / "context-profile.json")
    service = ContextPersonalizationService(
        config=ContextPersonalizationConfig(enabled=False),
        app_provider=lambda: "com.microsoft.VSCode",
        repository=repository,
    )

    assert service.capture() is None
    assert service.summary()["total"] == 0


def test_service_capture_fails_open_when_app_provider_raises():
    from vocal_more.application.context_personalization import (
        ContextPersonalizationService,
    )
    from vocal_more.domain.config_models import ContextPersonalizationConfig

    def failing_provider():
        raise RuntimeError("workspace unavailable")

    service = ContextPersonalizationService(
        config=ContextPersonalizationConfig(enabled=True),
        app_provider=failing_provider,
        repository=None,
    )

    assert service.capture() is None


def test_service_record_success_fails_open_when_repository_raises():
    from vocal_more.application.context_personalization import (
        ContextPersonalizationService,
    )
    from vocal_more.domain.app_context import AppContext
    from vocal_more.domain.config_models import ContextPersonalizationConfig

    class FailingRepository:
        def increment(self, _context):
            raise OSError("profile is read-only")

    service = ContextPersonalizationService(
        config=ContextPersonalizationConfig(enabled=True),
        app_provider=lambda: "com.microsoft.VSCode",
        repository=FailingRepository(),
    )

    service.record_success(
        AppContext(
            category="development",
            bundle_id="com.microsoft.VSCode",
        )
    )
