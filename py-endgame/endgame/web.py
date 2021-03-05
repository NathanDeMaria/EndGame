import asyncio
import re
import random
from logging import getLogger
from pathlib import Path
from typing import Dict, Union, Optional
import aiofiles
import aiohttp

from .config import CONFIG


logger = getLogger(__name__)


RequestParameters = Optional[Dict[str, Union[str, int]]]


class CacheableContent:
    """
    Something that can be cached,
    but we won't know if we want to cache it until some other
    business logic happens on the result.
    """

    def __init__(self, data: bytes, save_location: str):
        self._data = data
        self._save_location = save_location

    @property
    def data(self) -> bytes:
        """
        The data in this cacheable object
        """
        return self._data

    async def save_if_necessary(self):
        """
        Save this cacheable content,
        if it's not already on disk
        """
        # Don't save if it's already there
        save_path = Path(self._save_location)
        if save_path.is_file():
            return

        save_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(self._save_location, "wb") as file:
            await file.write(self._data)


async def get(url: str, parameters: RequestParameters = None) -> CacheableContent:
    """
    HTTP GET something, checking a cache of local files first.
    """
    param_string = _build_param_string(parameters)
    cache_path = re.sub("[:/\\.]", "", url + param_string)
    cache_file = Path(CONFIG.cache_dir, "web", cache_path)
    if cache_file.is_file():
        # read it
        async with aiofiles.open(str(cache_file), "rb") as file:
            content = await file.read()
    else:
        content = await _get_with_retries(url, parameters)
    return CacheableContent(content, str(cache_file))


async def _get_with_retries(url: str, parameters: RequestParameters) -> bytes:
    max_retries = 5
    for i in range(max_retries):
        try:
            return await _get_web(url, parameters)
        except (
            aiohttp.client_exceptions.ClientResponseError,
            aiohttp.client_exceptions.ClientConnectorError,
        ) as error:
            if i + 1 == max_retries:
                raise error
            # Exponential backoff w/ +/- 10% jitter
            sleep_duration_s = (0.95 + 0.1 * random.random()) * ((i + 1) ** 2)
            param_string = _build_param_string(parameters)
            full_url = f"{url}?{param_string}" if param_string else url
            logger.warning(
                "Struggling to get %s. Error: %s. Attempt number %d. Sleeping for %.02f",
                full_url,
                _get_error_message(error),
                i + 1,
                sleep_duration_s,
            )
            await asyncio.sleep(sleep_duration_s)
    raise Exception("Should never reach here, this is just for mypy")


def _get_error_message(
    error: Union[
        aiohttp.client_exceptions.ClientResponseError,
        aiohttp.client_exceptions.ClientConnectorError,
    ]
) -> str:
    if isinstance(error, aiohttp.client_exceptions.ClientResponseError):
        return f"Status code: {error.status}"
    return str(error)


async def _get_web(url: str, parameters: RequestParameters) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url, params=parameters, raise_for_status=True
        ) as response:
            return await response.read()


def _build_param_string(parameters: RequestParameters) -> str:
    return "&".join([f"{k}={v}" for k, v in parameters.items()]) if parameters else ""
