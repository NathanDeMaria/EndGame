import asyncio
from collections.abc import Coroutine
from typing import Any, Awaitable, TypeVar, Callable, Iterable, AsyncIterator


# It'd be neat to also be able to match types between the callable
# and the args passed in here...someday...
ReturnType = TypeVar("ReturnType")


async def apply_in_parallel(
    function: Callable[..., Coroutine[None, None, ReturnType]], args: Iterable[Any]
) -> AsyncIterator[ReturnType]:
    """
    Run a list of tasks in parallel
    """
    tasks: list[asyncio.Task[ReturnType]] = [
        asyncio.create_task(function(*arg_set))
        for arg_set in args
    ]
    for task in tasks:
        yield await task
