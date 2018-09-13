source('R/ncaa_fb.R')
source('R/progress.R')

library(readr)


all_ncaaf_games <- seq(2001, 2017) %>% 
  map_with_progress(get_ncaa_season, map_fn = map_df)
all_ncaaf_games %>% write_csv('ncaaf.csv')

flipped <- all_ncaaf_games %>%
  select(team = opponent,
         team_conf = opponent_conf,
         team_id = opponent_id,
         season)
conference_lookups <- all_ncaaf_games %>%
  select(team, team_conf, team_id, season) %>%
  bind_rows(flipped) %>%
  group_by(season, team_id, team) %>% 
  summarise(team_conf = first(team_conf))
