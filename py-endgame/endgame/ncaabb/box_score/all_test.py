from datetime import datetime, timezone
from typing import Any, AsyncIterator, List, Tuple
from unittest.mock import patch

from ...types import Game, Season, Week
from ..gender import NcaabbGender
from . import all as box_scores


def _game(day: int, game_id: str, *, completed: bool = True) -> Game:
    return Game(
        home="Home",
        home_score=70,
        away="Away",
        away_score=65,
        neutral_site=False,
        completed=completed,
        date=datetime(2025, 11, day, tzinfo=timezone.utc),
        game_id=game_id,
    )


class _CapturingApply:
    """Stands in for `apply_in_parallel` to record what got fetched."""

    def __init__(self) -> None:
        self.calls: List[List[Tuple[Any, ...]]] = []

    def __call__(self, _func: Any, args: Any) -> AsyncIterator[None]:
        self.calls.append(list(args))

        async def _nothing() -> AsyncIterator[None]:
            return
            yield

        return _nothing()


async def _run(season: Season, **kwargs: Any) -> _CapturingApply:
    apply = _CapturingApply()
    with patch.object(box_scores, "apply_in_parallel", apply):
        pulled = [
            box
            async for box in box_scores.get_season_box_scores(
                season, NcaabbGender.mens, **kwargs
            )
        ]
    assert pulled == []
    return apply


class TestSkippingUnfinishedGames:
    """A game that hasn't been played has no box score to ask ESPN for.

    Nothing unplayed reaches here while `get_games` drops them at the fetch.
    This is what has to hold before a season is allowed to carry them: an
    unplayed game left in the args costs a request on every run between now
    and kickoff, and `get_box_score` can only answer None.
    """

    async def test_an_unfinished_game_is_not_fetched(self) -> None:
        season = Season(
            [Week([_game(4, "final"), _game(5, "tipping-off", completed=False)], 1)],
            2025,
        )

        apply = await _run(season)

        assert apply.calls == [[(NcaabbGender.mens, "final")]]

    async def test_a_week_with_nothing_finished_is_not_fetched_at_all(self) -> None:
        """Not an empty request -- no request."""
        season = Season([Week([_game(4, "tipping-off", completed=False)], 1)], 2025)

        apply = await _run(season)

        assert apply.calls == []

    async def test_it_stacks_with_the_already_pulled_filter(self) -> None:
        season = Season(
            [
                Week(
                    [
                        _game(4, "pulled"),
                        _game(5, "new"),
                        _game(6, "tipping-off", completed=False),
                    ],
                    1,
                )
            ],
            2025,
        )

        apply = await _run(season, skip_game_ids={"pulled"})

        assert apply.calls == [[(NcaabbGender.mens, "new")]]
