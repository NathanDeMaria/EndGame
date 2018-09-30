.maybe_future <- function(season) {
  # Lame way of checking if I want to use the cache
  # by checking if the season is > the current year
  current_year <- as.POSIXlt(Sys.Date())$year + 1900
  season >= current_year
}
