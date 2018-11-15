library(readr)
library(purrr)
library(magrittr)
library(EndGame)

options(EndGame.cache_dir = './internet/')

# Get game scores for all the NFL games I can get from ESPN
all_nfl_games <- seq(2002, 2018) %>%
  map_with_progress(get_nfl_season, map_fn = map_df)

all_nfl_games %>% write_csv('nfl.csv')
