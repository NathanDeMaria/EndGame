library(readr)
library(magrittr)
library(EndGame)
library(dplyr)
library(purrr)

options(EndGame.cache_dir = './internet/')


fix_names <- function(x) {
  x[x == 'Army Black Knights'] <- 'Army Knights'
  x[x == 'Hawaii Warriors'] <- "Hawai'i Rainbow Warriors"
  x[x == 'Connecticut Huskies'] <- 'UConn Huskies'
  x[x == 'Southern Methodist Mustangs'] <- 'SMU Mustangs'
  x[x == 'Southern University Jaguars'] <- 'Southern Jaguars'
  x
}


all_ncaaf_games <- seq(2001, 2018) %>%
  map_with_progress(get_ncaa_season, map_fn = map_df) %>%
  mutate(team = fix_names(team),
         opponent = fix_names(opponent)) %>%
  write_csv('ncaaf.csv')

get_ncaa_season(2018, include_future = T) %>%
  write_csv('ncaaf_2018.csv')

flipped <- all_ncaaf_games %>%
  select(team = opponent,
         team_conf = opponent_conf,
         team_id = opponent_id,
         season)
conference_lookups <- all_ncaaf_games %>%
  select(team, team_conf, team_id, season) %>%
  bind_rows(flipped) %>%
  group_by(season, team_id, team) %>%
  summarise(team_conf = first(team_conf)) %>%
  write_csv('ncaaf_conferences.csv')
