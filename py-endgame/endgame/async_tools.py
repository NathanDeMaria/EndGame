import asyncio
from collections.abc import Coroutine
from typing import TypeVar, Callable, Iterable, AsyncIterator
from typing_extensions import TypeVarTuple


_ArgTypes = TypeVarTuple("_ArgTypes")
_ReturnType = TypeVar("_ReturnType")


async def apply_in_parallel(
    function: Callable[[*_ArgTypes], Coroutine[None, None, _ReturnType]],
    args: Iterable[tuple[*_ArgTypes]],
    max_parallel: int = 10,
) -> AsyncIterator[_ReturnType]:
    """
    Run a list of tasks in parallel
    """
    semaphore = asyncio.Semaphore(max_parallel)
    async def _limited_task(arg_set):
        async with semaphore:
            return await function(*arg_set)
    tasks: list[asyncio.Task[_ReturnType]] = [
        asyncio.create_task(_limited_task(arg_set)) for arg_set in args
    ]
    results = await asyncio.gather(*tasks)
    for task in results:
        yield task
