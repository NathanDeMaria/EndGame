parse_score <- function(event) {
  competition <- event$competitions[[1]]
  teams <- competition$competitors %>%
    map_chr(~.x$team$displayName)
  scores <- competition$competitors %>%
    map_int(~as.integer(.x$score))
  is_home <- competition$competitors %>%
    map_lgl(~.x$homeAway == 'home')
  conferences <- competition$competitors %>%
    map(~.x$team$conferenceId) %>%
    map_if(~is.null(.x), 'NULL') %>%
    as.character()

  if (sum(is_home) != 1) {
    if (neutral_site) {
      # Doesn't matter which I "mark" as home
      is_home = c(T, F)
    } else {
      stop("Neither team is 'home', be smarter.")
    }
  }

  neutral_site <- competition$neutralSite
  completed <- event$status$type$completed

  tibble(
    home = teams[is_home],
    home_score = scores[is_home],
    home_conference = conferences[is_home],
    away = teams[!is_home],
    away_score = scores[!is_home],
    away_conference = conferences[!is_home],
    date = lubridate::ymd_hm(event$date),

    neutral_site,
    completed
  )
}
