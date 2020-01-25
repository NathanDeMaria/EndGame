import asyncio
from typing import Any, Awaitable, TypeVar, Callable, Iterable, AsyncIterator


# TODO: figure out the magic to make the args for f typed
ReturnType = TypeVar('ReturnType')


async def apply_in_parallel(
        f: Callable[..., Awaitable[ReturnType]],
        args: Iterable[Any]
) -> AsyncIterator[ReturnType]:
    tasks = [asyncio.create_task(f(*arg_set)) for arg_set in args]
    for t in tasks:
        yield await t
