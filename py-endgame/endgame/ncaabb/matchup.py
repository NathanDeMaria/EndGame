from csv import DictWriter
from logging import getLogger
from typing import Dict, List, Optional, Tuple
from bs4 import BeautifulSoup, Tag

from ..async_tools import apply_in_parallel
from ..web import get

from .ncaabb import get_seasons
from .gender import NcaabbGender
from .possession_side import PossessionSide


logger = getLogger(__name__)


URL_FORMAT = "https://www.espn.com/{gender}-college-basketball/matchup?gameId={game_id}"
StatsTable = Dict[str, Tuple[str, str]]


async def save_possessions(gender: NcaabbGender, location: str = None):
    """
    Save a .csv with possession counts/results,
    to be joined with the NCAABB games .csv
    """
    if location is None:
        location = f"ncaa{gender.name[0]}bb_possessions.csv"

    seasons = await get_seasons(gender)

    # TODO: read the .csv and skip all the game ids we already have values for?

    rows = []
    for season in seasons:
        for week in season.weeks:
            logger.info("Getting matchups for %d %d", season.year, week.number)
            args = [(gender, game.game_id) for game in week.games]
            games = apply_in_parallel(get_possessions, args)
            async for sides in games:
                if sides is None:
                    continue
                for side in sides:
                    rows.append(side.to_dict())
    with open(location, "w") as file:
        writer = DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


# https://www.espn.com/mens-college-basketball/matchup?gameId=401263346
async def get_possessions(
    gender: NcaabbGender, game_id: str
) -> Optional[List[PossessionSide]]:
    """
    Get an estimate of the total number of possessions in a game
    (combined between the two teams)
    """
    url = URL_FORMAT.format(gender=gender.name, game_id=game_id)
    content = await get(url)

    try:
        soup = BeautifulSoup(content.data, features="html.parser")
        matchup_table = soup.select_one("div#gamepackage-matchup table.mod-data")
        if matchup_table is None:
            # Seems like some old games don't have this
            await content.save_if_necessary()
            return None
        stats = _read_stats_from_table(matchup_table)
        if stats is None:
            await content.save_if_necessary()
            return None

        n_possessions = _estimate_number_possessions(
            field_goal_attempts=sum(map(_get_denominator, stats[_FIELD_GOALS])),
            offensive_rebounds=sum(map(int, stats[_OFFENSIVE_REBOUNDS])),
            turnovers=sum(map(int, stats[_TURNOVERS])),
            free_throw_attempts=sum(map(_get_denominator, stats[_FREE_THROWS])),
        )

        possession_sides = [
            _build_possession_side(stats, n_possessions, i, game_id) for i in range(2)
        ]

    except Exception as err:
        logger.warning("Struggling with %s", url)
        raise err

    await content.save_if_necessary()

    return possession_sides


def _build_possession_side(
    stats: StatsTable,
    n_possessions: float,
    side: int,
    game_id: str,
) -> PossessionSide:
    fgm, fga = map(int, stats[_FIELD_GOALS][side].split("-"))
    ftm, fta = map(int, stats[_FREE_THROWS][side].split("-"))
    tpm, tpa = map(int, stats[_THREES][side].split("-"))
    zero, one, two, three = estimate_possession_results(
        possessions=n_possessions / 2,
        field_goal_makes=fgm,
        field_goal_att=fga,
        free_throw_makes=ftm,
        free_throw_att=fta,
        three_makes=tpm,
        three_att=tpa,
    )
    return PossessionSide(
        game_id=game_id,
        home_team=side == 1,
        zero_points=zero,
        one_point=one,
        two_points=two,
        three_points=three,
    )


def _get_denominator(num_denom: str) -> int:
    """
    Given a string in the format like "MAKE-ATT", get "ATT"
    """
    _, denom = num_denom.split("-")
    return int(denom)


_FIELD_GOALS = "FG"
_OFFENSIVE_REBOUNDS = "Offensive Rebounds"
_TURNOVERS = "Total Turnovers"
_FREE_THROWS = "FT"
_THREES = "3PT"


def _read_stats_from_table(table: Tag) -> Optional[StatsTable]:
    """
    There's a table on the "matchup" page for NCAABB
    Each row has the label, and then two columns
    with the values of that stat for each team
    """
    stats = {}
    for row in table.find_all("tr"):
        tds = row.find_all("td")
        if not tds:
            continue
        stat_name, t1_stat, t2_stat = [td.text.strip() for td in tds]
        stats[stat_name] = (t1_stat, t2_stat)

    # There's games with '--' as the stat for all teams
    # ex: https://www.espn.com/mens-college-basketball/matchup?gameId=283290036
    if all(("--" in stat) for stat in stats.values()):
        return None

    return stats


def _estimate_number_possessions(
    field_goal_attempts: int,
    offensive_rebounds: int,
    turnovers: int,
    free_throw_attempts: int,
) -> float:
    """
    Formula from
    https://kenpom.com/blog/national-efficiency/
    """
    return (
        field_goal_attempts
        - offensive_rebounds
        + turnovers
        + 0.475 * free_throw_attempts
    )


def estimate_possession_results(
    possessions: float,
    field_goal_makes: int,
    field_goal_att: int,
    three_makes: int,
    three_att: int,
    free_throw_makes: int,
    free_throw_att: int,
) -> Tuple[int, int, int, int]:
    """
    Guess the number of possessions that ended with _ points.
    This ignores and-1's, because those are hard.
    Instead, those will show up as extra free throws.
    Note that because of this estimation,
    the total points might not match up

    Parameters
    ----------
    possessions
        Estimate of the # of possessions in the game for one team
    field_goal_makes/att DOES include 3's.

    Returns
    -------
    Guess at the number of possessions that ended with (0, 1, 2, 3) points.
    """
    three_misses = three_att - three_makes
    two_misses = field_goal_att - field_goal_makes - three_misses
    zero_points = float(three_misses + two_misses)

    two_point = float(field_goal_makes - three_makes)
    three_point = three_makes

    free_throw_possessions = possessions - zero_points - two_point - three_point
    # Guess how many of them were zero/one/two point possessions
    # by looking at the proportion made
    # This could be a bit better by taking into account 1-and-1's with makes
    # but I don't think that matters enough right now
    if free_throw_makes > 0:
        free_throw_pct = free_throw_makes / free_throw_att
        one_shot, two_shots = _estimate_free_throw_shots(
            free_throw_possessions, free_throw_att
        )
        # Points from one-shot free throw possessions
        zero_points += one_shot * (1 - free_throw_pct)
        one_point = one_shot * free_throw_pct
        # Points from two-shot free throw possessions
        zero_points += two_shots * (1 - free_throw_pct) * (1 - free_throw_pct)
        one_point += 2 * two_shots * free_throw_pct * (1 - free_throw_pct)
        two_point += two_shots * free_throw_pct * free_throw_pct
    else:
        # No free throws made means no one point possessions
        one_point = 0
        # Could also make this free_throw_att,
        # but I'd have to guestimate the # of shooting fouls/1-and-1s.
        # Plus there's games like
        # https://www.espn.com/mens-college-basketball/matchup?gameId=243280158
        # where the possession estimate is way off anyway
        zero_points += free_throw_possessions

    # Round them all
    zero_points = round(zero_points)
    one_point = round(one_point)
    two_point = round(two_point)

    assert (
        abs(zero_points + one_point + two_point + three_point - possessions) <= 2
    ), "Estimates must add up to the number of possessions (or at least close)"
    return zero_points, one_point, two_point, three_point


def _estimate_free_throw_shots(
    free_throw_possessions: float, free_throw_attempts: int
) -> Tuple[float, float]:
    """
    Given the number of possessions that end with free throws,
    and the number of FTs attempted, we can guess how many
    were one/two shots because:

        free_throw_possessions = two_shots + one_shot
        free_throw_att = 2 * two_shots + one_shot

    Could be smarter and take into account 1-and-1s, but...not today
    """
    two_shots = free_throw_attempts - free_throw_possessions
    one_shot = free_throw_possessions - two_shots
    return one_shot, two_shots
