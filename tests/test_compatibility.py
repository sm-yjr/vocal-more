"""Tests for compatibility check and repair helpers."""

import yaml


def test_run_compatibility_check_and_repair_updates_config_and_dictionary(
    tmp_path, monkeypatch
):
    """The compatibility tool should normalize both persisted user files."""
    from vocal_more.compatibility import run_compatibility_check_and_repair
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    dict_path = tmp_path / "dictionary.yaml"

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(
            {
                "audio": {"gain": 4.0, "noise_gate": 0.2},
                "asr": {"model": "qwen3.5-omni-plus", "backend": "realtime_ws"},
            },
            f,
            allow_unicode=True,
        )

    with open(dict_path, "w", encoding="utf-8") as f:
        yaml.dump(
            {
                "entries": [
                    {"term": "Claude", "aliases": [["可劳德"], None, 1]},
                    {"term": "", "aliases": ["ignored"]},
                ]
            },
            f,
            allow_unicode=True,
        )

    results = run_compatibility_check_and_repair("config", "dictionary")
    repaired = {result.target: result for result in results}
    persisted_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    persisted_dictionary = yaml.safe_load(dict_path.read_text(encoding="utf-8"))

    assert repaired["config"].changed is True
    assert repaired["dictionary"].changed is True
    assert persisted_config["asr"]["backend"] == "omni_offline"
    assert "noise_gate" not in persisted_config["audio"]
    assert persisted_dictionary == {
        "entries": [
            {"term": "Claude", "aliases": ["可劳德", "1"]},
        ]
    }
    assert repaired["config"].backup_path.endswith("config.yaml.config-pre-repair.bak")
    assert repaired["dictionary"].backup_path.endswith(
        "dictionary.yaml.dictionary-pre-repair.bak"
    )
    assert (tmp_path / "config.yaml.config-pre-repair.bak").exists()
    assert (tmp_path / "dictionary.yaml.dictionary-pre-repair.bak").exists()
