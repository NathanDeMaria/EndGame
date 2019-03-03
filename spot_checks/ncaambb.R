library(tidyverse)
library(testthat)

context("NCAAMBB")
ncaambb_scores <- read_csv('ncaambb.csv')

test_that("No games are played twice", {
  duplicates <- ncaambb_scores %>%
    group_by(home, date) %>%
    summarise(count = n()) %>%
    filter(count > 1)
  expect_equal(nrow(duplicates), 0)
})
