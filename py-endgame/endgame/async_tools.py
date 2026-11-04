import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine, Iterable


async def apply_in_parallel[*ArgTypes, ReturnType](
    function: Callable[[*ArgTypes], Coroutine[None, None, ReturnType]],
    args: Iterable[tuple[*ArgTypes]],
    max_parallel: int = 10,
) -> AsyncIterator[ReturnType]:
    """
    Run a list of tasks in parallel
    """
    semaphore = asyncio.Semaphore(max_parallel)

    async def _limited_task(arg_set):
        async with semaphore:
            return await function(*arg_set)

    tasks: list[asyncio.Task[ReturnType]] = [
        asyncio.create_task(_limited_task(arg_set)) for arg_set in args
    ]
    results = await asyncio.gather(*tasks)
    for task in results:
        yield task
