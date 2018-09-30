library(httr)
library(magrittr)


get_cached <- function(url, query, cache_dir = getOption('EndGame.cache_dir', tempdir()), check_cache = T) {
  url_string <- gsub('[:/\\.]', '', url)
  param_string <- paste0(names(query), '=', query, collapse = '&')
  saved_path <- file.path(cache_dir, paste0(url_string, param_string, '.rds'))
  if(file.exists(saved_path) & check_cache) {
    return(readRDS(saved_path))
  }
  contents <- GET(url, query = query) %>% content()
  saveRDS(contents, saved_path)
  contents
}
