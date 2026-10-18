import asyncio
import random
import re
import time
from logging import getLogger
from pathlib import Path
from typing import Dict, Optional, Union
from urllib.parse import urlsplit

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
            aiohttp.ClientResponseError,
            aiohttp.ClientConnectorError,
            aiohttp.ClientPayloadError,
            aiohttp.ServerDisconnectedError,
        ) as error:
            # A 4xx is a request this code got wrong. Asking again with the
            # same bytes spends the whole backoff to be told so five times,
            # which is how a scoreboard range that ESPN stopped accepting
            # read as flakiness instead of as a broken URL. 429 is the one
            # that does mean "later": keep retrying that.
            if (
                isinstance(error, aiohttp.ClientResponseError)
                and 400 <= error.status < 500
                and error.status != 429
            ):
                raise error
            if i + 1 == max_retries:
                raise error
            # Exponential backoff w/ +/- 10% jitter
            sleep_duration_s = (0.95 + 0.1 * random.random()) * ((i + 1) ** 2)
            param_string = _build_param_string(parameters)
            full_url = f"{url}?{param_string}" if param_string else url
            logger.warning(
                "Struggling to get %s. Error: %s. Attempt %d. Sleeping for %.02f",
                full_url,
                _get_error_message(error),
                i + 1,
                sleep_duration_s,
            )
            await asyncio.sleep(sleep_duration_s)
    raise Exception("Should never reach here, this is just for mypy")


def _get_error_message(
    error: Union[
        aiohttp.ClientResponseError,
        aiohttp.ClientConnectorError,
        aiohttp.ClientPayloadError,
        aiohttp.ServerDisconnectedError,
    ],
) -> str:
    if isinstance(error, aiohttp.ClientResponseError):
        return f"Status code: {error.status}"
    return str(error)


async def _get_web(url: str, parameters: RequestParameters) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url, params=_cache_busted(url, parameters), raise_for_status=True
        ) as response:
            return await response.read()


# ESPN's JSON API sits behind a CDN that says `max-age=10` but serves an
# expired copy once more while it revalidates: the first request for a URL
# after its copy goes stale gets that stale copy, and only the request after
# it gets the refresh. So what a request sees is as old as the previous
# request for that exact URL -- and nobody else asks for ours. A week of
# NCAAFB games fetched hourly came back an hour behind: on 2026-10-18 the
# 18:03Z NFL pull still had the 17:00Z kickoffs as scheduled at 0-0, and a
# Saturday's games reached the play-by-play store a median 89 minutes after
# their last play instead of within the hour. Tested directly: a URL read
# twice 150s apart returned the first read's game clock both times, and a
# third read seconds later was live.
#
# A query parameter ESPN ignores makes every request a URL the CDN hasn't
# seen, so it goes to the origin and comes back current. Only this host's
# requests carry it -- it's the one measured, and the www.espn.com pages are
# only read for games that are long over.
_CACHE_BUSTED_HOSTS = ("site.api.espn.com",)
CACHE_BUSTER_PARAM = "_"


def _cache_busted(url: str, parameters: RequestParameters) -> RequestParameters:
    """
    The parameters to send for `url`: as given, plus a never-repeated
    `CACHE_BUSTER_PARAM` for the hosts whose CDN serves stale copies.

    Only the request is changed. The local cache file is named in `get` from
    the parameters as the caller gave them, so a cached response is still
    found under the same name as before.
    """
    if urlsplit(url).hostname not in _CACHE_BUSTED_HOSTS:
        return parameters
    return {**(parameters or {}), CACHE_BUSTER_PARAM: time.time_ns()}


def _build_param_string(parameters: RequestParameters) -> str:
    return "&".join([f"{k}={v}" for k, v in parameters.items()]) if parameters else ""
