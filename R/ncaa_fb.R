# @ESPN - I'm just gonna use this until you say stop
# Don't worry, I'm not making money off of it
NCAA_SCOREBOARD <- 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard'

NCAA_SEASON_TYPES <- list(
  regular = 2,
  post = 3
)


get_ncaa_season <- function(season, include_future = F) {
  # TODO: dynamically get number of weeks
  regular <- seq_len(15) %>%
    map_df(~.get_ncaa_week(season, .x, NCAA_SEASON_TYPES$regular, include_future))
  bowls <- .get_ncaa_week(season, 1, NCAA_SEASON_TYPES$post, include_future) %>%
    mutate(week = week + 15)
  rbind(regular, bowls) %>% mutate(season = season)
}


.get_ncaa_week <- function(season, week, season_type, include_future) {
  futile.logger::flog.info("Grabbing season %d week %d", season, week)
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

  games <- get_cached(NCAA_SCOREBOARD, query = params, check_cache = !.maybe_future(season)) %>%
    .[['events']] %>%
    map_df(parse_score)
  if(length(games) == 0) {
    # Happens :/ Like Week 15 of 2005 NCAAF
    futile.logger::flog.warn("No games found for season %d week %d", season, week)
    return(tibble())
  }
  games %>%
    filter(completed | include_future) %>%
    mutate(week = week)
}
