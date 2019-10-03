library(tidyverse)
library(EndGame)
library(lubridate)

options(EndGame.cache_dir = './internet/')

# Starts in 1999, but the numbers are garbage
# Also, outside of top 50 didn't have ratings until 2002
# and non-FBS didn't get them until 2012
ratings <- map_df(seq(2000, 2019), get_247_fb_recuriting_ratings) %>%
  write_csv('ncaaf_recruiting.csv')
