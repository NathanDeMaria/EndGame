import asyncio

from endgame_aws.cli import plays

if __name__ == "__main__":
    asyncio.run(plays("womens", "2026-03-01"))
