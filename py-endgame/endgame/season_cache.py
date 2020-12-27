import pickle
from pathlib import Path
from typing import Optional

from .config import CONFIG
from .types import Season


class SeasonCache:
    """
    Cache tool for saving whole season results for a league
    (post-parsing, as opposed to the web cache that's just for raw HTTP responses)
    """

    def __init__(self, league: str):
        self._league = league

    def save_to_cache(self, season: Season):
        """
        Add a season to the cache
        """
        path = self._build_cache_path(season.year)
        if path.is_file():
            raise ValueError(f"Trying to overwrite cache at {str(path)}")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), "wb+") as file:
            pickle.dump(season, file)

    def check_cache(self, season: int) -> Optional[Season]:
        """
        Get a season from the cache.
        Returns None if the season isn't found.
        """
        path = self._build_cache_path(season)
        if not path.is_file():
            return None
        with open(str(path), "rb+") as file:
            return pickle.load(file)

    def _build_cache_path(self, year: int) -> Path:
        return Path(CONFIG.cache_dir, "season", self._league, f"{year}.pkl")
