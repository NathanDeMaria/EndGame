import pickle
from pathlib import Path

from .config import CONFIG
from .types import Season


class SeasonCache:
    def __init__(self, league: str):
        self._league = league

    def save_to_cache(self, year: int, season: Season):
        path = self._build_cache_path(year)
        if path.is_file():
            raise ValueError(f"Trying to overwrite cache at {str(path)}")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), "wb+") as f:
            pickle.dump(season, f)

    def check_cache(self, season: int):
        path = self._build_cache_path(season)
        if not path.is_file():
            return None
        with open(str(path), "rb+") as f:
            return pickle.load(f)

    def _build_cache_path(self, year: int) -> Path:
        return Path(CONFIG.cache_dir, "season", self._league, f"{year}.pkl")
