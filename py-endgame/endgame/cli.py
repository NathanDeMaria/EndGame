import asyncio
from fire import Fire

from .nfl import update


class Main:
    def update(self, league: str):
        if league == 'nfl':
            asyncio.run(update())
            return
        # TODO: bake this in w/ a type instead of string matching
        raise NotImplementedError(f"Update not implemented for {league}")


def main():
    Fire(Main)