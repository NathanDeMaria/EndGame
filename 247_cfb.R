library(tidyverse)
library(EndGame)

options(EndGame.cache_dir = './internet/')

# Starts in 1999, but the numbers are garbage
# Also, outside of top 50 didn't have ratings until 2002
# and non-FBS didn't get them until 2012
ratings <- map_df(seq(2000, 2019), get_247_fb_recuriting_ratings) %>%
  write_csv('ncaaf_recruiting.csv')

# Team ratings by year
# <50 2000-2001
# All FBS(?) 2002-2011
# Lots 2012+
ratings %>% group_by(year) %>% summarise(rated_teams = sum(rating > 0)) %>%
  ggplot() + geom_line(aes(x = year, y = rated_teams)) +
  labs(x = 'Year', y = '# teams with non-zero ratings', title = 'Teams rated by year')

# Total points by year is increasing, so I should probably normalize that...
ratings %>% group_by(year) %>% summarise(total_points = sum(rating)) %>%
  ggplot() + geom_line(aes(x = year, y = total_points))

# Drop-off of ratings by ranking per year
ratings %>% mutate(year = as.factor(year)) %>%
  group_by(year) %>% mutate(rank = order(order(rating, decreasing = T))) %>%
  ggplot() + geom_line((aes(x = rank, y = rating, col = year))) +
  labs(x = 'Rank', y = 'Rating', title = 'Drop-off curves each year')

# Drop-off of ratings by ranking per year
ratings %>% group_by(year) %>% mutate(rank = order(order(rating, decreasing = T))) %>%
  ggplot() + geom_line((aes(x = rank, y = rating, col = as.factor(year)))) +
  labs(x = 'Rank', y = 'Rating', title = 'Drop-off curves each year')

normalize <- function(x) {
  (x - mean(x)) / sd(x)
}

# Same drop-off, but after normalizing
ratings %>% mutate(year = as.factor(year)) %>%
  group_by(year) %>% mutate(rating = normalize(rating)) %>%
  mutate(rank = order(order(rating, decreasing = T))) %>%
  ggplot() + geom_line((aes(x = rank, y = rating, col = year))) +
  labs(x = 'Rank', y = 'Rating', title = 'Drop-off curves each year')

# Yeah, I like that better. Would recommend normalizing by year
# but saving raw to leave that up to the application
