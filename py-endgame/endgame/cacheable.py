from pathlib import Path
from typing import List, Optional, TypeVar, Type
import aiofiles
from dataclasses_json import DataClassJsonMixin

from .config import CONFIG


T = TypeVar("T", bound=DataClassJsonMixin)


class DiskCache:
    """
    An object that can be cached.
    """

    def __init__(self, cacheable_class: Type[T], unique_attributes: List[str]):
        self._cacheable_class = cacheable_class
        self._unique_attribues = unique_attributes
        assert all(
            a in cacheable_class.__annotations__ for a in unique_attributes
        ), "Invalid attributes"

    async def save_to_cache(self, item: T):
        """
        Add an item to the cache
        """
        path = self._build_cache_path(
            **{a: getattr(item, a) for a in self._unique_attribues}
        )
        if path.is_file():
            raise ValueError(f"Trying to overwrite cache at {str(path)}")
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(str(path), "w") as file:
            await file.write(item.to_json())

    async def check_cache(self, **kwargs) -> Optional[T]:
        """
        Get an item from the cache given its unique attributes
        Return None on cache miss
        """
        assert set(kwargs.keys()) == set(
            self._unique_attribues
        ), "Invalid attributes used to check cache"
        path = self._build_cache_path(**kwargs)
        if not path.is_file():
            return None
        async with aiofiles.open(path) as file:
            raw = await file.read()
        return self._cacheable_class.from_json(raw)

    def _build_cache_path(self, **kwargs) -> Path:
        unique_name = "--".join([f"{k}-{v}" for k, v in kwargs.items()])
        return Path(
            CONFIG.cache_dir, self._cacheable_class.__name__, f"{unique_name}.json"
        )
