library(stringr)
library(tidyverse)
library(lubridate)


SPORTS247_CFB_FORMAT <- 'https://247sports.com/Season/{year}-Football/CompositeTeamRankings/'


#' Get the NCAAFB recuriting ratings for a year from 247sports
#'
#' @param year
#'
#' @return tibble with columns for team name, year, and rating
#' @export
#'
#' @examples
get_247_fb_recuriting_ratings <- function(year) {
  page <- 1
  team_ratings <- .get_page_ratings(year, page)
  all_ratings <- list()
  while (nrow(team_ratings)) {
    all_ratings <- c(all_ratings, list(team_ratings))
    page <- page + 1
    team_ratings <- .get_page_ratings(year, page)
  }
  bind_rows(all_ratings) %>%
    # AFAIK, the only one of these is a low ranked team in 2014
    # between California, Pa. and Concordia University
    filter(name != '')
}

.get_page_ratings <- function(year, page) {
  logging::loginfo('Year: %s\tPage: %s', year, page)
  # If we're past July of this year, recruiting is probably finalized
  write_to_cache <- Sys.Date() > ymd(str_glue('{year}-07-01'))
  query <- list(
    ViewPath='~/Views/SkyNet/InstitutionRanking/_SimpleSetForSeason.ascx',
    Page=page,
    `_`=1565460248472
  )
  team_rows <- SPORTS247_CFB_FORMAT %>% str_glue(year = year) %>%
    get_cached_html(query = query, write_to_cache = write_to_cache) %>%
    rvest::html_nodes('li.rankings-page__list-item')

  tibble(
    name = team_rows %>% rvest::html_node('div.team') %>% html_text() %>% str_trim(),
    rating = team_rows %>% rvest::html_node('div.points') %>% html_text() %>% str_trim() %>% as.numeric(),
    year
  )
}
