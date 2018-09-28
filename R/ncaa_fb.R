# TODO: DRY with nfl.R
library(dplyr)
library(httr)
library(purrr)

# @ESPN - I'm just gonna use this until you say stop
# Don't worry, I'm not making money off of it
NCAA_SCOREBOARD <- 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard'

SEASON_TYPES <- list(
  regular = 2,
  post = 3
)


get_ncaa_season <- function(season, include_future = F) {
  print(season)
  # TODO: dynamically get number of weeks
  regular <- seq_len(15) %>%
    map_df(~.get_week(season, .x, SEASON_TYPES$regular, include_future))
  bowls <- .get_week(season, 1, SEASON_TYPES$post, include_future) %>% 
    mutate(week = week + 15)
  rbind(regular, bowls)
}


.get_week <- function(season, week, season_type, include_future) {
  params <- list(
    lang = 'en',
    region = 'us',
    calendartype = 'blacklist',
    limit = 300,
    seasontype = season_type,
    dates = season,
    week = week,
    groups = 80  # this is "All FBS"
  )
  games <- GET(NCAA_SCOREBOARD, query = params) %>% content() %>%
    .[['events']] %>%
    map_df(.parse_score_row)
  if(length(games) == 0) {
    # Happens :/ Like Week 15 of 2005 NCAAF
    return(tibble())
  }
  games %>%
    filter(status == 'STATUS_FINAL'
           | (include_future & (status == 'STATUS_SCHEDULED'))) %>%
    mutate(week = week,
           season = season,
           score = as.integer(score),
           opponent_score = as.integer(opponent_score))
}


.parse_score_row <- function(event) {
  teams <- event$competitions[[1]]$competitors
  team_conf <- teams[[1]]$team$conferenceId
  if (teams[[1]]$homeAway != 'home'
      | teams[[2]]$homeAway != 'away') {
    stop("K, you actually have to flip the teams.")
  }
  if(is.null(team_conf)) {
    team_conf <- NA
  }
  opponent_conf <- teams[[2]]$team$conferenceId
  if(is.null(opponent_conf)) {
    opponent_conf <- NA
  }
  list(
    team = teams[[1]]$team$displayName,
    opponent = teams[[2]]$team$displayName,
    score = teams[[1]]$score,
    opponent_score = teams[[2]]$score,
    date = event$date,
    status = event$status$type$name,
    neutral_site = event$competitions[[1]]$neutralSite,
    
    team_conf = team_conf,
    opponent_conf = opponent_conf,
    team_id = teams[[1]]$id,
    opponent_id = teams[[2]]$id
  )
}
