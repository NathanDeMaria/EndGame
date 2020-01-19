import re
import aiofiles
import aiohttp
import asyncio
import random
from logging import getLogger
from pathlib import Path
from typing import Dict, Union, Optional

from .config import CONFIG


logger = getLogger(__name__)


RequestParameters = Optional[Dict[str, Union[str, int]]]


class CacheableContent:
    def __init__(self, data: bytes, save_location: str):
        self.data = data
        self._save_location = save_location
    
    async def save_if_necessary(self):
        # Don't save if it's already there
        save_path = Path(self._save_location)
        if save_path.is_file():
            return

        save_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(self._save_location, 'wb') as f:
            await f.write(self.data)


async def get(url: str, parameters: RequestParameters = None) -> CacheableContent:
    param_string = '&'.join([f'{k}={v}' for k, v in parameters.items()]) if parameters else ''
    cache_path = re.sub('[:/\\.]', '', url + param_string)
    cache_file = Path(CONFIG.cache_dir, 'web', cache_path)
    if cache_file.is_file():
        # read it
        async with aiofiles.open(str(cache_file), 'rb') as f:
            content = await f.read()
    else:
        content = await _get_with_retries(url, parameters)
    return CacheableContent(content, str(cache_file))


async def _get_with_retries(url: str, parameters: RequestParameters) -> bytes:
    for i in range(5):
        try:
            return await _get_web(url, parameters)
        except aiohttp.client_exceptions.ClientResponseError as e:
            sleep_duration_s = (0.95  + 0.1 * random.random()) * ((i + 1) ** 2)
            logger.warning(f"Struggling to get {url}. Status code {e.status}. Attempt number {i + 1}. Sleeping for {sleep_duration_s:.02f}")
            # Exponential backoff w/ +/- 10% jitter
            await asyncio.sleep(sleep_duration_s)
    raise e


async def _get_web(url: str, parameters: RequestParameters) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=parameters, raise_for_status=True) as response:
            return await response.read()
