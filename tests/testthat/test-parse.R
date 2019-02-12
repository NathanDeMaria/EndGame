context("test-parse")

test_that("parse gets data", {
  event <- list(
    competitions = list(list(
      competitors = list(
        list(
          homeAway='home',
          score='1',
          team=list(
            conferenceId='1',
            displayName='TeamOne'
          )
        ),
        list(
          conferenceId='2',
          homeAway='away',
          score='2',
          team=list(
            conferenceId='2',
            displayName='TeamTwo'
          )
        )
      ),
      neutralSite = T
    )),
    date = '2019-02-09T21:00Z',
    status = list(
      type = list(
        completed = T
      )
    )
  )

  score <- parse_score(event)

  expect_equal(score$home, 'TeamOne')
  expect_equal(score$home_score, 1)
  expect_equal(score$home_conference, '1')
  expect_equal(score$away, 'TeamTwo')
  expect_equal(score$away_score, 2)
  expect_equal(score$away_conference, '2')
  expect_equal(score$date, as.POSIXct('2019-02-09 21:00:00', tz = 'UTC'))
  expect_true(score$neutral_site)
  expect_true(score$completed)
})
