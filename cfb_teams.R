library(tidyverse)
library(rvest)
library(httr)
library(EndGame)
library(logging)

.grep_espn_team_id <- function(link) {
  str_match(link, 'id/([0-9]+)')[,2]
}

TEAM_FMT <- 'https://www.espn.com/college-football/team/_/id/{espn_team_id}'

# Links, for FBS schools at least
team_ids <- GET('https://www.espn.com/college-football/teams') %>% content() %>%
  html_nodes('section.TeamLinks') %>% html_node('a') %>% html_attr('href') %>%
  map_chr(.grep_espn_team_id)


.get_team_info <- function(espn_team_id) {
  loginfo('Getting team: %s', espn_team_id)
  site <- TEAM_FMT %>% str_glue(espn_team_id = espn_team_id) %>% get_cached_html()
  espn_location <- site %>% html_node('span.ClubhouseHeader__Location') %>% html_text()
  espn_display_name <- site %>% html_node('span.ClubhouseHeader__DisplayName') %>% html_text()
  tibble(
    espn_location,
    espn_display_name,
    espn_team_id
  )
}

team_info <- team_ids %>% map_with_progress(.get_team_info, map_fn = map_df)

# Attempt to find other teams by looking at everyone's schedules ####
SCHEDULE_FMT <- 'https://espn.com/college-football/team/schedule/_/id/{team_id}/season/{year}'

get_opponent_ids <- function(team_id, year) {
    SCHEDULE_FMT %>% str_glue(team_id = team_id, year = year) %>%
      GET() %>% content() %>%
      html_node('section.Table2__responsiveTable') %>% html_nodes('a') %>% html_attr('href') %>%
      keep(grepl('^/college-football/team/_/id/', .)) %>% .grep_espn_team_id() %>% unique()
}

for (team_id in team_info$espn_team_id) {
  opp_ids <- get_opponent_ids(team_id, 2019)
  new_teams <- opp_ids %>% discard(~. %in% team_info$espn_team_id)
  if(length(new_teams)) {
    more_info <- new_teams %>% map_df(.get_team_info)
    team_info <- bind_rows(team_info, more_info)
  }
}

team_info %>% write_csv('ncaaf_team_info.csv')
