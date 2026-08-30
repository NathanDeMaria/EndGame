ESPN_SPORTS_API_BASE = "https://site.api.espn.com/apis/site/v2/sports"

# How far past today a league pulled a day at a time asks for, once it's
# fetching fixtures as well as results.
#
# The leagues pulled a week at a time get their schedule for nothing: one
# request for week N comes back with every game in it, played or not. A
# league walked by day has to spend a request per day it wants to see, and
# none of them cache -- `espn_games.get_games` refuses to cache a response
# holding an unfinished game, so every future day is re-fetched on every
# run, forever. A whole remaining season of that is ~250 requests a run for
# the NHL and ~390 for NCAABB (five groups deep in the postseason window).
#
# So it's a window rather than the rest of the season: a week is what
# downstream needs to see what's coming, at 7 requests a run for the NHL and
# the WNBA. Per-league on `DailyLeague` so one league can widen it without
# the others paying for it.
DEFAULT_LOOKAHEAD_DAYS = 7
