from csv import DictWriter
from dataclasses import dataclass
from typing import AsyncIterable, Dict, Tuple, Union
from bs4 import BeautifulSoup

from ..web import get
from .teams import NflTeam, PRO_FOOTBALL_REFERENCE_SHORT_NAMES

URL_FORMAT = "https://www.pro-football-reference.com/years/{year}/coaches.htm"


@dataclass
class CoachRow:
    """
    A row to write to the coaches .csv
    """

    year: int
    team: NflTeam
    coach: str

    def to_dict(self) -> Dict[str, Union[str, int]]:
        """
        Serialize as a savable set of key-value pairs
        """
        return {
            "year": self.year,
            "team": self.team.name,
            "coach": self.coach,
        }


async def save_coaches(location: str = "nfl_coaches.csv"):
    """
    Pull the latest NFL coaches and save to a .csv
    """
    with open(location, "w") as file:
        # pylint: disable=no-member
        writer = DictWriter(file, fieldnames=CoachRow.__annotations__.keys())
        writer.writeheader()
        async for row in get_all_coaches():
            writer.writerow(row.to_dict())


async def get_all_coaches() -> AsyncIterable[CoachRow]:
    """
    Pull all the NFL coaches
    """
    # This isn't the first year it's available, I just picked one
    for year in range(1990, 2021):
        async for coach, team in get_coaches(year):
            yield CoachRow(
                year=year,
                coach=coach,
                team=team,
            )


async def get_coaches(year: int) -> AsyncIterable[Tuple[str, NflTeam]]:
    """
    Get all the coaches in the NFL for a given year

    Yields
    ------
    name
        A coach's name
    team
        The NFL team that coach coached.
    """
    content = await get(URL_FORMAT.format(year=year))
    soup = BeautifulSoup(content.data, features="html.parser")
    rows = soup.find(id="coaches").find_all("tr")  # type: ignore[union-attr]
    _, column_headers, *data_rows = rows
    column_names = [h.text for h in column_headers.find_all("th")]

    for data_row in data_rows:
        cell_values = [i.text for i in data_row.children]
        assert len(column_names) == len(cell_values)
        row_dict = dict(zip(column_names, cell_values))
        team = PRO_FOOTBALL_REFERENCE_SHORT_NAMES[row_dict["Tm"]]
        yield row_dict["Coach"], team
    await content.save_if_necessary()
