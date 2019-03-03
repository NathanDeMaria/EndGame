library(EndGame)
library(lubridate)
library(purrr)
library(dplyr)
library(magrittr)
library(readr)
library(futile.logger)

options(EndGame.cache_dir = './internet/')

get_ncaabb_season_scores <- function(year) {
  flog.info("Getting year %d", year)
  regular_season <- seq(ISOdate(year, 11, 1), ISOdate(year + 1, 3, 30), by = 'day') %>%
    # No idea what's wrong with this date...the full page also fails
    # http://www.espn.com/mens-college-basketball/scoreboard/_/group/50/date/20071118
    # Maybe try these dates again later?
    # Filtering these dates to conferences/Top 25 seems to work fine
    # just getting all D1 fails
    discard(~.x == ISOdate(2002, 1, 14)) %>%
    discard(~.x == ISOdate(2005, 2, 20)) %>%
    discard(~.x == ISOdate(2006, 2, 12)) %>%
    discard(~.x == ISOdate(2007, 1, 18)) %>%
    discard(~.x == ISOdate(2007, 2, 15)) %>%
    discard(~.x == ISOdate(2007, 2, 18)) %>%
    discard(~.x == ISOdate(2007, 3, 2)) %>%
    discard(~.x == ISOdate(2013, 11, 27)) %>%
    discard(~.x == ISOdate(2014, 11, 14)) %>%
    discard(~.x == ISOdate(2014, 12, 19)) %>%
    as.Date() %>%
    map_with_progress(function(d) {get_ncaabb_scores(d, 'womens')}, map_df)
  post_tournaments <- seq(ISOdate(year + 1, 3, 1), ISOdate(year + 1, 4, 30), by = 'day') %>%
    # TODO: only remove the groups that error?
    discard(~.x == ISOdate(2007, 3, 1)) %>%
    discard(~.x == ISOdate(2007, 3, 2)) %>%
    discard(~.x == ISOdate(2007, 3, 31)) %>%
    as.Date() %>%
    map_with_progress(function(d) {
      NCAAMB_TOURNAMENT_GROUPS %>% map_df(~get_ncaabb_scores(d, 'womens', .x))
    }, map_df)

  bind_rows(regular_season, post_tournaments) %>%
    mutate(season = year) %>%
    # weeks start on Monday
    mutate(week = floor_date(date - days(1), 'weeks') + days(1)) %>%
    # week -> week_num
    mutate(week = group_indices(., week))
}

old_file <- 'old_ncaawbb.csv'
old_seasons <- if(file.exists(old_file)) {
  read_csv(old_file)
} else {
  seq(2001, 2017) %>%
    map_df(get_ncaabb_season_scores) %>%
    write_csv(old_file)
}

current_scores <- get_ncaabb_season_scores(2018)
scores <- bind_rows(old_seasons, current_scores) %>%
  write_csv('ncaawbb.csv')

