library(tidyverse)
library(rvest)
library(httr)
library(EndGame)
library(logging)

options(EndGame.cache_dir = './internet/')

.grep_espn_team_id <- function(link) {
  str_match(link, 'id/([0-9]+)')[,2]
}

TEAM_FMT <- 'https://www.espn.com/college-football/team/_/id/{espn_team_id}'

# Links, for FBS schools at least
# https://www.espn.com/college-football/teams/_/group/80
# https://www.espn.com/college-football/teams/_/group/81
get_team_ids <- function(group_id) {
  str_glue('https://www.espn.com/college-football/teams/_/group/{group_id}', group_id = group_id) %>%
    GET() %>% content() %>%
    html_nodes('section.TeamLinks') %>% html_node('a') %>% html_attr('href') %>%
    map_chr(.grep_espn_team_id)
}
team_ids <- c(
  get_team_ids(80),  # FBS
  get_team_ids(81),  # FCS
  get_team_ids(35)   # DII/DIII
)


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
  opp_links <- tryCatch({
    SCHEDULE_FMT %>% str_glue(team_id = team_id, year = year) %>%
      get_cached_html() %>%
      html_node('section.Table2__responsiveTable') %>% html_nodes('a') %>% html_attr('href') %>%
      keep(grepl('^/college-football/team/_/id/', .))
  }, error = function(e) {
    if (!grepl('500', e$message)) {
      stop(e)
    }
    logwarn("Error on the page for team %s in %s", team_id, year)
    character()
  })
  if (!length(opp_links)) {
    # In case a team played no games this year
    return(character())
  }
  opp_links %>% .grep_espn_team_id() %>% unique()
}


for (year in seq(2019, 2003)) {
  mattered <- T
  while(mattered) {
    loginfo("Last loop added at least one team. Cycling through again for %s.", year)
    mattered <- F
    for (team_id in team_info$espn_team_id) {
      opp_ids <- get_opponent_ids(team_id, year)
      new_teams <- opp_ids %>% discard(~. %in% team_info$espn_team_id)
      if(length(new_teams)) {
        more_info <- new_teams %>% map_df(.get_team_info)
        team_info <- bind_rows(team_info, more_info)
        mattered <- T
      }
    }
  }
}


team_info %>% write_csv('ncaaf_team_info.csv')
