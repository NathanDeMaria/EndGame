from enum import Enum


class NcaabbGender(Enum):
    """
    Names of NCAABB basketball genders.
    Value will match the string ESPN's API expects
    """

    womens = "womens"
    mens = "mens"
