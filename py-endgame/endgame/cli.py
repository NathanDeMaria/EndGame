import asyncio

from fire import Fire

from .ncaabb import (
    NcaabbGender,
    save_box_scores,
)
from .ncaabb import (
    update as update_ncaabb,
)
from .ncaafb import update as update_ncaafb
from .nfl import save_coaches, save_spreads, update


class Main:
    def save_nfl_coaches(self):
        asyncio.run(save_coaches())

    def save_nfl_spreads(self):
        asyncio.run(save_spreads())

    def update(self, league: str):
        if league == "nfl":
            asyncio.run(update())
            return
        elif league == "ncaafb":
            asyncio.run(update_ncaafb())
            return
        elif league == "ncaawbb":
            asyncio.run(update_ncaabb(NcaabbGender.womens))
            # A little inefficient, since this re-reads the games
            # Oh well, this is easy
            # asyncio.run(save_possessions(NcaabbGender.womens))
            asyncio.run(save_box_scores(NcaabbGender.womens))
            return
        elif league == "ncaambb":
            asyncio.run(update_ncaabb(NcaabbGender.mens))
            # asyncio.run(save_possessions(NcaabbGender.mens))
            asyncio.run(save_box_scores(NcaabbGender.mens))
            return
        # TODO: bake this in w/ a type instead of string matching
        raise NotImplementedError(f"Update not implemented for {league}")


def main():
    Fire(Main)
