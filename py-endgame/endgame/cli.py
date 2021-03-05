import asyncio
from fire import Fire

from .nfl import update, save_coaches, save_spreads
from .ncaafb import update as update_ncaafb
from .ncaabb import update as update_ncaabb, NcaabbGender, save_possessions


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
            asyncio.run(save_possessions(NcaabbGender.womens))
            return
        elif league == "ncaambb":
            asyncio.run(update_ncaabb(NcaabbGender.mens))
            # asyncio.run(save_possessions(NcaabbGender.mens))
            return
        # TODO: bake this in w/ a type instead of string matching
        raise NotImplementedError(f"Update not implemented for {league}")


def main():
    Fire(Main)
