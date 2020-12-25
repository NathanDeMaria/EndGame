library(testthat)
library(tidyverse)


ncaa_fb <- read_csv('../py-endgame/ncaaf.csv')
# One team per row, instead 
ncaa_fb_long <- ncaa_fb %>% select(team = home, season, week, opponent = away, team_score = home_score, opp_score = away_score) %>%
  bind_rows(ncaa_fb %>% select(team = away, season, week, opponent = home, team_score = away_score, opp_score = home_score))

# The dip in 2014 seems a little weird
ncaa_fb %>% group_by(season) %>%
  count() %>%
  ggplot() +
  geom_line(aes(x = season, y = n)) +
  labs(y = '# games')

test_that("Correct Husker game counts", {
  # At least for one team I'm okay manually looking up
  husker_seeasons <- ncaa_fb_long %>% filter(team == 'Nebraska Cornhuskers') %>%
    group_by(season) %>% count()
  
  expected_games <- c(
    13, 14,
    # Starting in 2003 https://www.espn.com/college-football/team/schedule/_/id/158/season/2003
    13, 11, 12, 13, 12,
    13, 14, 14, 13, 14,
    13, 13, 13, 13, 12,
    12, 12
  )
  expect_equal(husker_seeasons$n, expected_games)
})

test_that("Teams only play one game per regular-season week", {
  weekly_counts <- ncaa_fb_long %>%
    filter(week > 1, week < 15) %>% 
    group_by(team, season, week) %>%
    count()
  
  expect_equal(weekly_counts$n, rep(1, nrow(weekly_counts)))
})
