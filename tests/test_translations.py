"""Translation files must stay structurally in sync with English.

A missing key makes Home Assistant fall back to English silently, and a
dropped placeholder ({service}, {warnings}) breaks string formatting in
the UI at runtime -- neither shows up in normal testing.
"""

import json
import re
from pathlib import Path

import pytest

TRANSLATIONS = Path("custom_components/weather_uploader/translations")
LANGUAGES = ["nl", "fr"]


def _load(lang):
    return json.loads((TRANSLATIONS / f"{lang}.json").read_text(encoding="utf-8"))


def _keys(obj, prefix=""):
    found = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            found.add(f"{prefix}.{key}")
            found |= _keys(value, f"{prefix}.{key}")
    return found


def _strings(obj, prefix=""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _strings(value, f"{prefix}.{key}")
    elif isinstance(obj, str):
        yield prefix, obj


@pytest.mark.parametrize("lang", LANGUAGES)
def test_translation_keys_match_english(lang):
    """Every English key exists in the translation, and no extras."""
    english = _keys(_load("en"))
    translated = _keys(_load(lang))
    assert not english - translated, f"{lang}: missing {sorted(english - translated)}"
    assert not translated - english, f"{lang}: extra {sorted(translated - english)}"


@pytest.mark.parametrize("lang", LANGUAGES)
def test_translation_placeholders_preserved(lang):
    """{service} and {warnings} must survive translation."""
    english = dict(_strings(_load("en")))
    translated = dict(_strings(_load(lang)))
    for path, source in english.items():
        expected = set(re.findall(r"\{(\w+)\}", source))
        actual = set(re.findall(r"\{(\w+)\}", translated[path]))
        assert expected == actual, f"{lang} {path}: expected {expected}, got {actual}"


@pytest.mark.parametrize("lang", LANGUAGES)
def test_technical_identifiers_not_translated(lang):
    """Field names and proper nouns must stay verbatim."""
    english = dict(_strings(_load("en")))
    translated = dict(_strings(_load(lang)))
    for term in ("rain_rate", "OpenWeatherMap", "Home Assistant", "CWOP", "APRS-IS"):
        for path, source in english.items():
            if term in source:
                assert term in translated[path], f"{lang} {path}: lost '{term}'"
