library(httr)
library(dplyr)
library(purrr)
library(rvest)

# @ESPN - I'm just gonna use this until you say stop
# Don't worry, I'm not making money off of it
NCAAMB_SCOREBOARD <- 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard'

get_events <- function(on_date, verbose = F) {
  params <- list(
    lang = 'en',
    region = 'us',
    calendartype = 'blacklist',
    limit = 300,
    dates = as.character(score_date, '%Y%m%d'),
    groups = 50
  )
  
  body <- GET(NCAAMB_SCOREBOARD, query = params) %>% content()
  
  map_fn <- if(verbose) {
    map_with_progress
  } else {
    map
  }
  game_tables <- body$events %>% map_fn(.get_play_by_play_html) %>% map(.parse_pbp)
  had_pbp <- game_tables %>% map_lgl(~nrow(.x) > 0)
  teams <- body$events[had_pbp] %>%
    map(~.x$competitions[[1]]$competitors %>%
          map_chr(~.x$team$displayName))
  game_ids <- body$events[had_pbp] %>% map(~.x$id)
  
  teams %>% map2(game_tables[had_pbp], ~.y %>% mutate(home = .x[1], away = .x[2])) %>% 
    map2(game_ids, ~.x %>% mutate(game_id = .y)) %>% 
    bind_rows() %>% 
    mutate(game_date = on_date)
}

.get_play_by_play_html <- function(event) {
  is_pbp <- event$links %>% map_lgl(~(.x$rel[[1]] == 'pbp'))
  if(!any(is_pbp)) {
    return()
  }
  pbp_link <- event$links[is_pbp][[1]]$href
  GET(pbp_link) %>% content()
}

.parse_pbp <- function(pbp_html) {
  # Srry...but works
  if(is.null(pbp_html)) {
    return(tibble())
  }
  period_divs <- pbp_html %>% 
    html_nodes(xpath = '//div[contains(@id, "gp-quarter-")]')
  
  period_numbers <- period_divs %>%
    html_attr('id') %>%
    strsplit('-') %>%
    map_int(~as.integer(.x[[3]]))
  period_tables <- period_divs %>% 
    map(~.parse_period(.x))
  period_numbers %>%
    map2_df(period_tables, ~cbind(period_number = .x, .y)) %>%
    bind_rows()
}

.parse_period <- function(period_div) {
  # TODO: should I just use rvest::html_table here?
  period_plays <- period_div %>%
    html_nodes(xpath = './/tr[td[@class="game-details"]]')
  seconds_left <- period_plays %>%
    html_node('.time-stamp') %>%
    html_text() %>%
    strsplit(':') %>%
    map_dbl(.convert_str_to_seconds)
  play_text <- period_plays %>%
    html_node('.game-details') %>%
    html_text()
  scores <- period_plays %>%
    html_node('.combined-score') %>%
    html_text() %>%
    .convert_str_to_score() %>% 
    do.call(rbind, .)
  
  tibble(seconds_left, home_score = scores[,1], away_score = scores[,2], play_text)
}


# Convert 1:50 to 110
.convert_str_to_seconds <- function(s) {
  pieces <- s %>% strsplit(':') %>% as.integer()
  pieces[1] * 60 + pieces[2]
}


# "10 - 9" to `[1] 10 9`
.convert_str_to_score <- function(x) {
  x %>% strsplit('-') %>% map(trimws) %>% map(as.integer)
}
