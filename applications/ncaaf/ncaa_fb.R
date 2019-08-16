library(readr)
library(magrittr)
library(EndGame)
library(dplyr)
library(purrr)

options(EndGame.cache_dir = './internet/')

# https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard Params: lang=en&region=us&calendartype=blacklist&limit=300&seasontype=2&dates=2001&week=9&groups=80
fix_names <- function(x) {
  # Some of these names change across seasons
  # Since I use this as their ID, that's not ok
  x[x == 'Army Knights'] <- 'Army Black Knights'
  x[x == 'Hawaii Warriors'] <- "Hawai'i Rainbow Warriors"
  x[x == 'Connecticut Huskies'] <- 'UConn Huskies'
  x[x == 'Southern Methodist Mustangs'] <- 'SMU Mustangs'
  x[x == 'Southern University Jaguars'] <- 'Southern Jaguars'
  x
}


all_ncaaf_games <- seq(2001, 2018) %>%
  map_with_progress(get_ncaa_season, map_fn = map_df) %>%
  mutate(home = fix_names(home),
         away = fix_names(away)) %>%
  write_csv('ncaaf.csv')

flipped <- all_ncaaf_games %>%
  select(home = away,
         home_conference = away_conference,
         season)
conference_lookups <- all_ncaaf_games %>%
  select(home, home_conference, season) %>%
  bind_rows(flipped) %>%
  group_by(season, home) %>%
  summarise(home_conference = first(home_conference)) %>%
  write_csv('ncaaf_conferences.csv')
