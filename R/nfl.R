library(dplyr)
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
  "Arizona Cardinals", "Atlanta Falcons", "Baltimore Ravens", "Buffalo Bills",
  "Carolina Panthers", "Chicago Bears", "Cincinnati Bengals", "Cleveland Browns",
  "Dallas Cowboys", "Denver Broncos", "Detroit Lions", "Green Bay Packers",
  "Houston Texans", "Indianapolis Colts", "Jacksonville Jaguars", "Kansas City Chiefs",
  "Los Angeles Chargers", "Los Angeles Rams", "Miami Dolphins", "Minnesota Vikings",
  "New England Patriots", "New Orleans Saints", "New York Giants", "New York Jets",
  "Oakland Raiders", "Philadelphia Eagles", "Pittsburgh Steelers", "San Francisco 49ers",
  "Seattle Seahawks", "Tampa Bay Buccaneers", "Tennessee Titans", "Washington Redskins"
)


#' Get NFL Seasons
#'
#' Grab the scores of all games for an NFL season
#'
#' @param season
#'
#' @return tibble of games
#' @export
#'
#' @examples
get_nfl_season <- function(season) {
  regular <- seq_len(17) %>%
    map_df(~.get_nfl_week(season, .x, SEASON_TYPES$regular))
  # Was the pro-bowl always 4?
  post <- seq_len(5) %>%
    map_df(~.get_nfl_week(season, .x, SEASON_TYPES$playoffs)) %>%
    dplyr::filter(home %in% REAL_TEAMS) %>%
    dplyr::mutate(week = week + 17)

  rbind(regular, post) %>%
   dplyr::mutate(
      home = .replace_names(home),
      away = .replace_names(away)
    )
}

.replace_names <- function(names) {
  names %>% sub(pattern = 'San Diego', replacement = 'Los Angeles') %>%
    sub(pattern = 'St. Louis', replacement = 'Los Angeles')
}


.get_nfl_week <- function(season, week, season_type) {
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
    map_df(parse_score) %>%
    dplyr::filter(completed) %>%
    dplyr::mutate(week = week,
           season = season,
           home_score = as.integer(home_score),
           away_score = as.integer(away_score)) %>%
    dplyr::select(-completed)
}
