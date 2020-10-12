from enum import Enum, auto


class NflTeam(Enum):
    """
    All NFL teams
    """

    bengals = auto()
    browns = auto()
    ravens = auto()
    steelers = auto()

    colts = auto()
    jaguars = auto()
    texans = auto()
    titans = auto()

    bills = auto()
    dolphins = auto()
    jets = auto()
    patriots = auto()

    broncos = auto()
    chargers = auto()
    chiefs = auto()
    raiders = auto()

    bears = auto()
    lions = auto()
    packers = auto()
    vikings = auto()

    buccaneers = auto()
    falcons = auto()
    panthers = auto()
    saints = auto()

    cowboys = auto()
    eagles = auto()
    giants = auto()
    washington = auto()

    cardinals = auto()
    rams = auto()
    seahawks = auto()
    niners = auto()


PRO_FOOTBALL_REFERENCE_SHORT_NAMES = {
    "ARI": NflTeam.cardinals,
    "ATL": NflTeam.falcons,
    "BAL": NflTeam.ravens,
    "BUF": NflTeam.bills,
    "CAR": NflTeam.panthers,
    "CHI": NflTeam.bears,
    "CIN": NflTeam.bengals,
    "CLE": NflTeam.browns,
    "DAL": NflTeam.cowboys,
    "DEN": NflTeam.broncos,
    "DET": NflTeam.lions,
    "GNB": NflTeam.packers,
    "HOU": NflTeam.texans,
    "IND": NflTeam.colts,
    "JAX": NflTeam.jaguars,
    "KAN": NflTeam.chiefs,
    "LAC": NflTeam.chargers,
    "LAR": NflTeam.rams,
    "LVR": NflTeam.raiders,
    "MIA": NflTeam.dolphins,
    "MIN": NflTeam.vikings,
    "NOR": NflTeam.saints,
    "NWE": NflTeam.patriots,
    "NYG": NflTeam.giants,
    "NYJ": NflTeam.jets,
    "OAK": NflTeam.raiders,
    "PHI": NflTeam.eagles,
    "PHO": NflTeam.cardinals,
    "PIT": NflTeam.steelers,
    "RAI": NflTeam.raiders,
    "RAM": NflTeam.rams,
    "SDG": NflTeam.chargers,
    "SEA": NflTeam.seahawks,
    "SFO": NflTeam.niners,
    "STL": NflTeam.rams,
    "TAM": NflTeam.buccaneers,
    "TEN": NflTeam.titans,
    "WAS": NflTeam.washington,
}
