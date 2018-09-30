library(dplyr)
# library(tibble)
library(httr)
library(purrr)

# @ESPN - I'm just gonna use this until you say stop
# Don't worry, I'm not making money off of it
NFL_SCOREBOARD <- 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard'

SEASON_TYPES <- list(
  preseason = 1,
  regular = 2,
  playoffs = 3
)

REAL_TEAMS <- c(
  "49ers", "Bears", "Bengals", "Bills", "Broncos", "Browns", "Buccaneers", "Cardinals",
  "Chargers", "Chiefs", "Colts", "Cowboys", "Dolphins", "Eagles", "Falcons", "Giants",
  "Jaguars", "Jets", "Lions", "Packers", "Panthers", "Patriots", "Raiders", "Rams",
  "Ravens", "Redskins", "Saints", "Seahawks", "Steelers", "Texans", "Titans", "Vikings"
)


get_nfl_season <- function(season) {
  regular <- seq_len(17) %>%
    map_df(~.get_week(season, .x, SEASON_TYPES$regular))
  # Was the pro-bowl always 4?
  post <- seq_len(5) %>%
    map_df(~.get_week(season, .x, SEASON_TYPES$playoffs)) %>%
    filter(team %in% REAL_TEAMS) %>%
    mutate(week = week + 17)

  rbind(regular, post)
}


.get_week <- function(season, week, season_type) {
  params <- list(
    lang = 'en',
    region = 'us',
    calendartype = 'blacklist',
    limit = 32,
    seasontype = season_type,
    dates = season,
    week = week
  )
  get_cached(NFL_SCOREBOARD, query = params, check_cache = !.maybe_future(season)) %>%
    .[['events']] %>%
    map_df(.parse_score_row) %>%
    filter(completed) %>%
    mutate(week = week,
           season = season,
           score = as.integer(score),
           opponent_score = as.integer(opponent_score)) %>%
    select(-completed)
}


.parse_score_row <- function(event) {
  # TODO: mark home team? mark neutral site?
  teams <- event$competitions[[1]]$competitors
  list(
    team = teams[[1]]$team$name,
    opponent = teams[[2]]$team$name,
    score = teams[[1]]$score,
    opponent_score = teams[[2]]$score,
    date = event$date,
    completed = event$status$type$completed
  )
}
