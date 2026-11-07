import pytest

from .sports247_to_espn import Translator

_ESPN = {"Ohio State", "Nicholls", "Ashland", "Stillman", "BYU", "UConn"}


@pytest.fixture
def translator() -> Translator:
    return Translator(_ESPN)


def test_names_espn_already_has_pass_through(translator: Translator) -> None:
    assert translator.translate("Ohio State") == "Ohio State"


def test_lookup_renames(translator: Translator) -> None:
    assert translator.translate("Brigham Young") == "BYU"
    assert translator.translate("Connecticut") == "UConn"


def test_drops_state_university_and_college(translator: Translator) -> None:
    assert translator.translate("Nicholls State") == "Nicholls"
    assert translator.translate("Ashland University") == "Ashland"
    assert translator.translate("Stillman College") == "Stillman"


def test_unknown_names_have_no_match(translator: Translator) -> None:
    assert translator.translate("Nowhere Tech") is None
