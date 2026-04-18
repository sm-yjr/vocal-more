"""Tests for dictionary helpers."""

import yaml


def test_dictionary_service_builds_asr_corpus_without_config_cycle(tmp_path):
    """The service should build corpus text from pure entries plus caller-supplied extras."""
    from vocal_more.application.dictionary_service import DictionaryService
    from vocal_more.domain.dictionary_models import DictEntry
    from vocal_more.infrastructure.dictionary_repository import DictionaryRepository

    service = DictionaryService(DictionaryRepository(base_dir=tmp_path))
    service.replace_entries(
        [
            DictEntry(term="Vocal More", aliases=["vocal mall"]),
            DictEntry(term="阿里云百炼", aliases=["阿里云白练"]),
        ]
    )

    assert (
        service.build_asr_corpus_text(["DashScope", "Vocal More"])
        == "Vocal More\n阿里云百炼\n\nDashScope"
    )


def test_dictionary_service_normalizes_aliases_case_insensitively_for_ascii(tmp_path):
    """ASCII aliases should normalize case-insensitively without matching inside larger tokens."""
    from vocal_more.application.dictionary_service import DictionaryService
    from vocal_more.domain.dictionary_models import DictEntry
    from vocal_more.infrastructure.dictionary_repository import DictionaryRepository

    service = DictionaryService(DictionaryRepository(base_dir=tmp_path))
    service.replace_entries([DictEntry(term="OpenAI", aliases=["open ai", "OA"])])

    assert service.normalize_terms("open ai codex") == "OpenAI codex"
    assert service.normalize_terms("broadway") == "broadway"


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


def test_add_entry_normalizes_html_alias_payloads(tmp_path, monkeypatch):
    """HTML settings payloads should be normalized into a clean alias list."""
    from vocal_more.config import Config
    from vocal_more.dictionary import Dictionary

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))

    dictionary = Dictionary()
    dictionary.add_entry("Claude", '["可劳德", "克劳德"]')
    dictionary.add_entry("Claude", [" 可劳德 ", ["小克"]])

    assert [(entry.term, entry.aliases) for entry in dictionary.entries] == [
        ("Claude", ["可劳德", "克劳德", "小克"])
    ]

    saved = yaml.safe_load((tmp_path / "dictionary.yaml").read_text(encoding="utf-8"))
    assert saved == {
        "entries": [
            {"term": "Claude", "aliases": ["可劳德", "克劳德", "小克"]}
        ]
    }


def test_load_sanitizes_malformed_aliases_without_crashing(tmp_path, monkeypatch):
    """Malformed dictionary alias formats should load safely and auto-rewrite."""
    from vocal_more.config import Config
    from vocal_more.dictionary import Dictionary

    dict_path = tmp_path / "dictionary.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))

    with open(dict_path, "w") as f:
        yaml.dump(
            {
                "entries": [
                    {"term": "Vocal More", "aliases": "vocal mall, VM"},
                    {"term": "Claude", "aliases": [["可劳德"], None, 1, {"bad": "shape"}]},
                    {"term": "", "aliases": ["ignored"]},
                    "bad-entry",
                ]
            },
            f,
            allow_unicode=True,
        )

    dictionary = Dictionary()
    persisted = yaml.safe_load(dict_path.read_text(encoding="utf-8"))

    assert [(entry.term, entry.aliases) for entry in dictionary.entries] == [
        ("Vocal More", ["vocal mall", "VM"]),
        ("Claude", ["可劳德", "1"]),
    ]
    assert dictionary.normalize_terms("我在用 vocal mall 和 VM") == "我在用 Vocal More 和 Vocal More"
    assert persisted == {
        "entries": [
            {"term": "Vocal More", "aliases": ["vocal mall", "VM"]},
            {"term": "Claude", "aliases": ["可劳德", "1"]},
        ]
    }
