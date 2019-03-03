library(EndGame)
library(lubridate)
library(futile.logger)
library(magrittr)
library(purrr)
library(tidyverse)

options(EndGame.cache_dir = './internet/')

# does this get tourney games?
get_ncaabb_season_scores <- function(year) {
  flog.info("Getting year %d", year)
  regular_season <- seq(ISOdate(year, 11, 1), ISOdate(year + 1, 3, 30), by = 'day') %>%
    # No idea what's wrong with this date...the full page also fails
    # http://www.espn.com/mens-college-basketball/scoreboard/_/group/50/date/20071118
    # Maybe try these dates again later?
    # Filtering these dates to conferences/Top 25 seems to work fine
    # just getting all D1 fails
    discard(~.x == ISOdate(2001, 3, 16)) %>%
    discard(~.x == ISOdate(2001, 3, 17)) %>%
    discard(~.x == ISOdate(2001, 3, 18)) %>%
    discard(~.x == ISOdate(2007, 11, 18)) %>%
    discard(~.x == ISOdate(2009, 11, 18)) %>%
    discard(~.x == ISOdate(2013, 11, 8)) %>%
    discard(~.x == ISOdate(2014, 12, 4)) %>%
    discard(~.x == ISOdate(2015, 3, 7)) %>%
    as.Date() %>%
    map_with_progress(function(d) {get_ncaabb_scores(d, 'mens')}, map_df)
  post_tournaments <- seq(ISOdate(year + 1, 3, 1), ISOdate(year + 1, 4, 30), by = 'day') %>%
    as.Date() %>%
    map_with_progress(function(d) {
      NCAAMB_TOURNAMENT_GROUPS %>% map_df(~get_ncaabb_scores(d, 'mens', .x))
    }, map_df)

  bind_rows(regular_season, post_tournaments) %>%
    mutate(season = year) %>%
    # weeks start on Monday
    mutate(week = floor_date(date - days(1), 'weeks') + days(1)) %>%
    # week -> week_num
    mutate(week = group_indices(., week)) %>%
    # Some random completed game in 2003 :shrug:
    filter(away_score > 0, home_score > 0)
}

old_file <- 'old_ncaambb.csv'
old_seasons <- if(file.exists(old_file)) {
  read_csv(old_file)
} else {
  seq(2001, 2017) %>%
    map_df(get_ncaabb_season_scores) %>%
    write_csv(old_file)
}

current_scores <- get_ncaabb_season_scores(2018)
scores <- bind_rows(old_seasons, current_scores) %>%
  write_csv('ncaambb.csv')
