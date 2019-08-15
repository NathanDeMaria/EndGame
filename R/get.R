library(magrittr)
library(stringr)


get_cached <- function(url, query = NULL, cache_dir = getOption('EndGame.cache_dir', tempdir()), check_cache = T, write_to_cache = T) {
  url_string <- .clean_for_path(url)
  param_string <- if (is.null(query)) {
    ''
  } else {
    paste0(names(query), '=', query, collapse = '&') %>% .clean_for_path()
  }
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
  if (write_to_cache) {
    saveRDS(contents, saved_path)
  }
  contents
}


#' Get cached
#'
#' Checks a cache (just a directory) before making a GET
#' Saves the content if there wasn't a file locally
#'
#' @param url
#' @param query
#' @param cache_dir
#' @param check_cache
#' @param write_to_cache
#'
#' @return contents from the url.
#' @export
#'
#' @examples
get_cached_html <- function(url, query = NULL, cache_dir = getOption('EndGame.cache_dir', tempdir()), check_cache = T, write_to_cache = T) {
  # Switching to saving as XML because of https://github.com/tidyverse/rvest/issues/181
  # potentially only need this when you're loading/saving lists instead of full HTML pages
  url_string <- .clean_for_path(url)
  param_string <- if (is.null(query)) {
    ''
  } else {
    paste0(names(query), '=', query, collapse = '&') %>% .clean_for_path()
  }
  saved_path <- file.path(cache_dir, paste0(url_string, param_string, '.html'))
  if(file.exists(saved_path) & check_cache) {
    return(xml2::read_html(saved_path))
  }
  response <- RETRY('GET', url, query = query, pause_base = 4, times = 10)
  if(response$status_code != 200) {
    stop(stringr::str_glue("Error: GET returned {response$status_code}. ",
                           "URL: {url} ",
                           "Params: {param_string}"))
  }
  contents <-  response %>% content()
  if (write_to_cache) {
    write_html(contents, saved_path)
  }
  contents
}

.clean_for_path <- function(s) {
  gsub('[:/\\.]', '', s)
}
