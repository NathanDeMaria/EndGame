from csv import DictWriter
import re
from dataclasses import dataclass
from typing import AsyncIterable, Dict, Iterable, Optional, Tuple
from bs4 import BeautifulSoup, Tag

from .cities import CITIES
from .teams import NflTeam
from ..web import get


URL_FORMAT = "http://www.footballlocks.com/nfl_point_spreads_{week}.shtml"


@dataclass
class Game:
    """
    A game with a spread, where
    a negative number means the home team is favored
    """

    home: NflTeam
    away: NflTeam
    week: int
    season: int
    spread: float

    def to_dict(self) -> Dict:
        """
        Serializable form of a game
        """
        return {
            "home": self.home.name,
            "away": self.away.name,
            "week": self.week,
            "season": self.season,
            "spread": self.spread,
        }


async def save_spreads(location: str = "nfl_spreads.csv"):
    """
    Save all the spreads we can find for a

    This gets most of the games 2006-2019
    """
    weeks = [f"week_{i}" for i in range(1, 18)]
    weeks += [
        "wild_card_playoff_games",
        "divisional_playoff_games",
        "conference_championship_playoff_games",
        "super_bowl",
    ]
    with open(location, "w") as file:
        # pylint: disable=no-member
        writer = DictWriter(file, fieldnames=Game.__annotations__.keys())
        writer.writeheader()

        for week in weeks:
            async for game in get_spreads(week):
                writer.writerow(game.to_dict())


async def get_spreads(week: str) -> AsyncIterable[Game]:
    """
    Get all the spreads for an NFL week.
    Yep, it's arranged by week of the season,
    as in each get grabs the spreads for a single week
    during every season since 2006
    """
    content = await get(URL_FORMAT.format(week=week))
    soup = BeautifulSoup(content.data, features="html.parser")
    tables = soup.find_all("table")

    for table in tables:
        if row := table.findChild("tr", recursive=False):
            if td_tag := row.findChild("td", recursive=False):
                if center := td_tag.findChild("center", recursive=False):
                    games = _to_games(td_tag, center.text)
                    for game in games:
                        yield game

    await content.save_if_necessary()


def _to_games(td_tag: Tag, header_text: str) -> Iterable[Game]:
    week_str, season_str = (
        header_text.strip()
        .split("\n")[0]
        .replace("Closing NFL Point Spreads ", "")
        .split(",")[:2]
    )
    if "Wild Card" in week_str:
        week = 18
    elif "Divisional" in week_str:
        week = 19
    elif "Conference" in week_str:
        week = 20
    elif "Super" in week_str:
        week = 22
    else:
        week = int(week_str.replace("Week ", "").strip())
    season = int(season_str.strip().split("-")[0])

    main_table = td_tag.select_one("p > span > table")
    for home, away, spread in _parse_table(main_table):
        yield Game(
            home=home,
            away=away,
            week=week,
            season=season,
            spread=spread,
        )
    # MNF table...assumes there's only one per week :shrug:
    other_table = td_tag.select_one("p > p > span > table")
    if other_table is None:
        # Week 6 is broken, only one team from the main block
        # shows up, and the Monday night table is totally gone
        return
    for home, away, spread in _parse_table(other_table):
        yield Game(
            home=home,
            away=away,
            week=week,
            season=season,
            spread=spread,
        )


def _parse_table(table: Tag) -> Iterable[Tuple[NflTeam, NflTeam, float]]:
    # Skip the first one b/c it's headers
    for table_row in table.find_all("tr")[1:]:
        parsed = _parse_row(table_row)
        if parsed is not None:
            yield parsed


def _parse_row(table_row: Tag) -> Optional[Tuple[NflTeam, NflTeam, float]]:
    """
    Returns
    -------
    List of tuples where:
    home_team
        name of the home team
    away_team
        name of the away team
        Assumes there'll be an "At ..." if the second team is away,
        and nothing if the second team is home (no way to know neutral site)
    home_spread
        Points home team gets (positive if the home team is the underdog)
    """
    tds = [td.text for td in table_row.find_all("td")]
    if len(tds) < 4:
        # Sometimes the whole table doesn't seem to come through
        # like 2017 week 10
        return None
    # Sometimes there's also an over-under at the end here?
    _, team1, spread, team2, *_ = tds
    if spread == "PPD":
        # 2014 week 12 had a postponed game
        return None
    if "PK" in spread:
        spread = 0.0
    else:
        spread = float(spread)
    team1 = tidy_name(team1)
    team2 = tidy_name(team2)
    if "At " in team1:
        home = team1.replace("At ", "", 1)
        away = team2
        spread = -spread
    else:
        home = team2.replace("At ", "", 1)
        away = team1
    return CITIES[home], CITIES[away], spread


LONDON_RE = re.compile(
    r"\(?([Aa]t)? ?(London|Wembley|Toronto|Mexico City|Ford Field|TCF Bank Stadium)\)?"
)


def tidy_name(name: str) -> str:
    """
    Remove garbage from the team name.
    Usually something like an location if it's not
    the home stadium of either team.
    """
    name = re.sub(LONDON_RE, "", name).replace("(Detroit)", "")
    return " ".join([s.strip() for s in name.split("\n") if s.strip()])
