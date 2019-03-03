library(httr)
library(magrittr)
library(stringr)


get_cached <- function(url, query, cache_dir = getOption('EndGame.cache_dir', tempdir()), check_cache = T) {
  url_string <- gsub('[:/\\.]', '', url)
  param_string <- paste0(names(query), '=', query, collapse = '&')
  saved_path <- file.path(cache_dir, paste0(url_string, param_string, '.rds'))
  if(file.exists(saved_path) & check_cache) {
    return(readRDS(saved_path))
  }
  response <- RETRY('GET', url, query = query, pause_base = 4, times = 10)
  if(response$status_code != 200) {
    stop(stringr::str_glue("Error: GET returned {response$status_code}. ",
                           "URL: {url} ",
                           "Params: {param_string}"))
  }
  contents <-  response %>% content()
  saveRDS(contents, saved_path)
  contents
}
