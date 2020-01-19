library(testthat)
library(tidyverse)


nfl <- read_csv('../py-endgame/nfl.csv')
# One team per row, instead 
nfl_long <- nfl %>% select(team = home, season, week) %>%
  bind_rows(nfl %>% select(team = away, season, week))

test_that("All games in every season", {
  n_games <- nfl %>% group_by(season) %>% count()
  expected_games <- nfl %>% group_by(season) %>%
    summarise(n_teams = n_distinct(home)) %>% 
    # Playoffs always had 12 teams, so 11 games
    mutate(games = n_teams * 16 / 2 + 11)

  joined <- n_games %>% inner_join(expected_games, by = 'season')
  past_seasons <- joined %>%
    # B/c the current season might not be finished yet
    filter(max(joined$season) != season) %>% 
    # ESPN api isn't returning any of the 1999 playoffs for some reason
    filter(season != 1999)
  expect_equal(past_seasons$n, past_seasons$games)
})

test_that("Teams only play one game per week", {
  weekly_counts <- nfl_long %>%
    group_by(team, season, week) %>% count()
  
  expect_equal(weekly_counts$n, rep(1, nrow(weekly_counts)))
})

test_that("Teams play at least 16 games", {
  season_counts <- nfl_long %>% group_by(season, team) %>% count()
  
  expect_true(all(season_counts$n >= rep(16, nrow(season_counts))))
})
