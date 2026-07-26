"""i18n integrity — every language must define the same keys, and formatting works."""

from utils import i18n


def test_all_languages_share_the_same_keys():
    reference = set(i18n.TEXTS[i18n.DEFAULT_LANG])
    for lang in i18n.LANGS:
        assert set(i18n.TEXTS[lang]) == reference, f"{lang} key mismatch"


def test_every_language_is_present():
    for lang in i18n.LANGS:
        assert lang in i18n.TEXTS


def test_translate_falls_back_and_formats():
    # Unknown user → default language; known key returns a string.
    assert isinstance(i18n.t(0, "welcome"), str)
    # Formatting placeholders resolve without error.
    assert "5" in i18n.t(0, "retrying", sec=5)
    # Unknown key degrades to the key itself rather than raising.
    assert i18n.t(0, "no_such_key") == "no_such_key"


def test_new_keys_exist():
    for key in ("probing", "yt_choose", "downloading_pct", "merging"):
        for lang in i18n.LANGS:
            assert key in i18n.TEXTS[lang], f"{lang} missing {key}"


def test_progress_formats():
    assert "42" in i18n.t(0, "downloading_pct", pct=42)
