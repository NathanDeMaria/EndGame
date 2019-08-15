import os
import pandas as pd

from .sports247_to_espn import Translator
from .testing import set_mistmatch


ENDGAME_ROOT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', '..',
)
SPORTS247_FILE = os.path.join(ENDGAME_ROOT_DIR, 'ncaaf_recruiting.csv')
ESPN_TEAMS_FILE = os.path.join(ENDGAME_ROOT_DIR, 'ncaaf_team_info.csv')


def test_all_match():
    espn_teams = pd.read_csv(ESPN_TEAMS_FILE)
    translator = Translator(set(espn_teams.espn_location))

    sports247 = pd.read_csv(SPORTS247_FILE)
    total_misses = 0
    for _, row in sports247.iterrows():
        # Make sure I didn't have too many with good recruits slip through
        # B/c I'm probably going to treat them as zeroes
        translated = translator.translate(row['name'])
        if translated is None:
            total_misses += 1
            assert row.rating < 65.0
        assert total_misses < 100
