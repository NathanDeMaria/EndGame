# ------------------------------------------------------------------------------
# Football finals watcher
# ------------------------------------------------------------------------------
# What starts the intraday football chains now, instead of an hourly schedule.
# A Lambda runs every five minutes, reads the day's scoreboards, and starts a
# league's `football_intraday` chain when a game has gone final since the last
# run it started. Between games it doesn't read ESPN; with nothing new it does
# nothing. The code and the reasons are in finals_watcher/.
#
# The chain is unchanged, and so is what follows it: cassandra's EPA and
# quarterback sweeps start on the chain's last job succeeding (an EventBridge
# rule in that repo), so a final reaches the play-by-play store, and the
# indexes read off it, within a few minutes of a game ending.
locals {
  finals_watcher_name = "${var.resource_name_prefix}-football-finals-watcher"
  finals_watcher_key  = "finals-watcher/state.json"

  # What the watcher needs per league: the chain to start, and every job that
  # writes the league's plays -- the 8am chain's as well as the chain's own,
  # since either one running is a reason not to start another.
  finals_watcher_leagues = {
    for league in local.football_leagues : league => {
      state_machine_arn = module.football_intraday[league].state_machine_arn
      job_names = concat(
        module.football_intraday[league].job_names,
        module.football[league].job_names,
      )
    }
  }
}

# The handler and the module it imports, and nothing else: no tests, and no
# packages, since a Lambda's Python already has boto3 and the rest is the
# standard library.
data "archive_file" "finals_watcher" {
  type        = "zip"
  output_path = "${path.module}/.build/finals_watcher.zip"

  source {
    content  = file("${path.module}/finals_watcher/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/finals_watcher/finals.py")
    filename = "finals.py"
  }
}

resource "aws_cloudwatch_log_group" "finals_watcher" {
  name              = "/aws/lambda/${local.finals_watcher_name}"
  retention_in_days = 30
}

resource "aws_lambda_function" "finals_watcher" {
  function_name    = local.finals_watcher_name
  description      = "Start the intraday football chains when games go final"
  role             = aws_iam_role.finals_watcher.arn
  runtime          = "python3.13"
  handler          = "handler.handler"
  filename         = data.archive_file.finals_watcher.output_path
  source_code_hash = data.archive_file.finals_watcher.output_base64sha256

  # A tick is a handful of scoreboard reads and a few Batch calls: about two
  # seconds -- or, with a game in its last minutes, up to `RECHECK_BUDGET`
  # (200s) of re-reading it. The rest is headroom for ESPN being slow, which
  # its own 20s timeout per read bounds, and stays under the five-minute
  # tick so one invocation ends before the next is due.
  timeout     = 290
  memory_size = 256

  # One tick at a time. Two at once would both see the same new finals and
  # both start a run for them.
  reserved_concurrent_executions = 1

  environment {
    variables = {
      STATE_BUCKET = local.s3_bucket_name
      STATE_KEY    = local.finals_watcher_key
      JOB_QUEUE    = local.job_queue_arn
      LEAGUES      = jsonencode(local.finals_watcher_leagues)
    }
  }

  depends_on = [aws_cloudwatch_log_group.finals_watcher]
}

# A failed tick goes to the shared failure topic: the email every failed Batch
# job sends. No retries -- the next tick, five minutes on, is the retry. The
# handler fails a tick for a run of ESPN failures, not for one, so a blip is a
# log line rather than an email.
resource "aws_lambda_function_event_invoke_config" "finals_watcher" {
  function_name                = aws_lambda_function.finals_watcher.function_name
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 300

  destination_config {
    on_failure {
      destination = local.batch.failure_topic_arn
    }
  }
}

resource "aws_scheduler_schedule" "finals_watcher" {
  name       = "${local.finals_watcher_name}-schedule"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  state               = var.finals_watcher_enabled ? "ENABLED" : "DISABLED"
  schedule_expression = "rate(5 minutes)"

  target {
    arn      = aws_lambda_function.finals_watcher.arn
    role_arn = aws_iam_role.scheduler_role.arn

    retry_policy {
      maximum_retry_attempts       = 0
      maximum_event_age_in_seconds = 300
    }
  }
}

# ------------------------------------------------------------------------------
# IAM for the watcher
# ------------------------------------------------------------------------------
resource "aws_iam_role" "finals_watcher" {
  name = "${local.finals_watcher_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "finals_watcher" {
  name = "${local.finals_watcher_name}-policy"
  role = aws_iam_role.finals_watcher.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.finals_watcher.arn}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "arn:aws:s3:::${local.s3_bucket_name}/${local.finals_watcher_key}"
      },
      {
        # Without it a missing object reads as AccessDenied rather than
        # NoSuchKey, and the handler only takes the second for a first run.
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = "arn:aws:s3:::${local.s3_bucket_name}"
        Condition = {
          StringEquals = { "s3:prefix" = local.finals_watcher_key }
        }
      },
      {
        # ListJobs takes no resource: it's the queue's whole listing or none.
        Effect   = "Allow"
        Action   = "batch:ListJobs"
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = "states:StartExecution"
        Resource = [for league in values(local.finals_watcher_leagues) : league.state_machine_arn]
      },
      {
        # The on-failure destination publishes as the function.
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = local.batch.failure_topic_arn
      },
    ]
  })
}
