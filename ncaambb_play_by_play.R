source('R/ncaam_bb.R')
source('R/progress.R')

# First day of the 17-18 season
season_tipoff <- as.Date('2017-11-10')

this_season <- seq(season_tipoff, Sys.Date(), by = 1)
data_dir <- 'data/'

safely_save <- function(d) {
  date_str <- as.character(d, '%Y%m%d')
  cat('\n', date_str, '\n')
  tryCatch({
    events <- get_events(d, verbose = T)
    saveRDS(events, sprintf('%s/%s.rds', data_dir, date_str))
  }, error = function(e) {
    cat(e, '\n')
  })
}

this_season %>% map_with_progress(safely_save)
