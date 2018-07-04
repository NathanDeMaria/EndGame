library(dplyr)
library(purrr)

#' With Progress
#' 
#' Acts like %>% map(f), except with a progress bar.
#'
#' @param x Some listy input
#' @param f Some function we're mapping over x
#'
#' @return
#' @export
#'
#' @examples
map_with_progress <- function(x, f, map_fn = map) {
  pb <- progress_estimated(length(x))
  f_with_progress <- function(x) {
    pb$tick()$print()
    f(x)
  }
  x %>% map_fn(f_with_progress)
}
