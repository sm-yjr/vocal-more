"""Tests for dictionary helpers."""

import yaml


def test_build_asr_corpus_text_deduplicates_and_appends_extra_terms(
    tmp_path, monkeypatch
):
    """Dictionary terms should become a compact ASR corpus."""
    from vocal_more.config import Config, reload_config
    from vocal_more.dictionary import reload_dictionary, build_asr_corpus_text

    config_path = tmp_path / "config.yaml"
    dict_path = tmp_path / "dictionary.yaml"

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "asr": {
                    "use_dictionary_corpus": True,
                    "extra_corpus_terms": ["DashScope", "Vocal More"],
                }
            },
            f,
        )

    with open(dict_path, "w") as f:
        yaml.dump(
            {
                "entries": [
                    {"term": "Vocal More", "aliases": ["vocal mall"]},
                    {"term": "阿里云百炼", "aliases": ["阿里云白练"]},
                ]
            },
            f,
            allow_unicode=True,
        )

    reload_config()
    reload_dictionary()

    corpus_text = build_asr_corpus_text()
    assert corpus_text == "Vocal More\n阿里云百炼\n\nDashScope"


def test_normalize_terms_respects_boundaries(tmp_path, monkeypatch):
    """Alias normalization should avoid replacing inside other tokens."""
    from vocal_more.config import Config, reload_config
    from vocal_more.dictionary import reload_dictionary, normalize_terms

    config_path = tmp_path / "config.yaml"
    dict_path = tmp_path / "dictionary.yaml"

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(dict_path, "w") as f:
        yaml.dump(
            {
                "entries": [
                    {"term": "Vocal More", "aliases": ["vocal mall", "VM"]},
                    {"term": "阿里云百炼", "aliases": ["阿里云白练"]},
                ]
            },
            f,
            allow_unicode=True,
        )

    reload_config()
    reload_dictionary()

    assert normalize_terms("我在用阿里云白练和vocal mall") == "我在用阿里云百炼和Vocal More"
    assert normalize_terms("rapid vmware rollout") == "rapid vmware rollout"
    assert normalize_terms("VM 已经接好") == "Vocal More 已经接好"
