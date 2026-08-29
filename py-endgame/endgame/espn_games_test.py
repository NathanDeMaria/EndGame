"""
Telling league play from the exhibitions ESPN returns alongside it.

Every case here is a real event, with the season and competition types
ESPN actually served for it -- the ids are the real ones, so a case can be
checked against the API it came from.
"""

from typing import Dict, Optional

import pytest

from .espn_games import is_league_game


def _event(
    season_type: Optional[int],
    competition_type: Optional[str],
    name: str = "Away at Home",
    event_id: str = "1",
) -> Dict:
    season = {} if season_type is None else {"season": {"type": season_type}}
    competition: Dict = {}
    if competition_type is not None:
        competition["type"] = {"abbreviation": competition_type}
    return dict(id=event_id, name=name, competitions=[competition], **season)


@pytest.mark.parametrize(
    "season_type,competition_type,name",
    [
        # An ordinary game, in both leagues and across the eras -- NHL 2002
        # through 2025 and WNBA 2003 through 2026 all come back like this.
        (2, "STD", "New York Rangers at Carolina Hurricanes"),
        # The postseason is kept whatever its rounds are called.
        (3, "RD16", "Washington Capitals at New York Rangers"),
        (3, "QTR", "Tampa Bay Lightning at Washington Capitals"),
        (3, "SEMI", "Dallas Stars at Edmonton Oilers"),
        (3, "FINAL", "Vancouver Canucks at Boston Bruins"),
        # The WNBA's Commissioner's Cup final: one game a year, two real
        # teams, so it counts.
        (2, "CC", "Indiana Fever at Minnesota Lynx"),
    ],
)
def test_league_play_is_kept(
    season_type: int, competition_type: str, name: str
) -> None:
    assert is_league_game(_event(season_type, competition_type, name))


@pytest.mark.parametrize(
    "season_type,competition_type,name",
    [
        # Preseason, which is where the games against teams that aren't in
        # the league live.
        (1, "STD", "Florida Panthers at Carolina Hurricanes"),
        (1, "STD", "Adler Mannheim at Chicago Blackhawks"),
        (1, "STD", "NIGERIA at Indiana Fever"),
        (1, "EXH", "China at Los Angeles Sparks"),
        # The All-Star game is filed under the *regular* season, which is
        # why the season type alone can't be the check.
        (2, "ALLSTAR", "Team Staal at Team Lidstrom"),
        (2, "ALLSTAR", "TEAM COLLIER at TEAM CLARK"),
        # The 2023 NHL All-Star replaced the single game with a bracket
        # between the divisions. Its games are "SEMI", the same name a
        # conference final has -- the season type is what separates them.
        (2, "SEMI", "Pacific at Central"),
        # The 4 Nations Face-Off, played in the All-Star's slot in 2025.
        (2, "QRR", "USA at Canada"),
    ],
)
def test_exhibitions_are_dropped(
    season_type: int, competition_type: str, name: str
) -> None:
    assert not is_league_game(_event(season_type, competition_type, name))


def test_semi_final_depends_on_the_season_type() -> None:
    """
    The case that makes this take both fields: same competition name, one
    a conference final and one an All-Star bracket game.
    """
    assert is_league_game(_event(3, "SEMI", "Dallas Stars at Edmonton Oilers"))
    assert not is_league_game(_event(2, "SEMI", "Pacific at Central"))


def test_unknown_regular_season_competition_is_dropped() -> None:
    """
    An unrecognized competition costs real games rather than admitting a
    team that doesn't exist -- see the allowlist's comment.
    """
    assert not is_league_game(_event(2, "WHATEVER"))


def test_missing_fields_do_not_admit_an_event() -> None:
    """
    A response missing the blocks this reads mustn't skip the check by
    omission.
    """
    assert not is_league_game(_event(None, None))
    assert not is_league_game(_event(None, "ALLSTAR"))
    assert not is_league_game({"id": "1", "competitions": [{}]})
    # ... but a normal competition with no season block still reads as the
    # regular season, which is the one league game shape that lacks one.
    assert is_league_game(_event(None, "STD"))
