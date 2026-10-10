provider "aws" {
  region = var.aws_region
}

locals {
  # The account's Batch stack owns the job queue and the data bucket, and
  # publishes both (see the data lookup below). Reading them from there rather
  # than re-declaring them here means there's nothing to keep in sync by hand,
  # and a rename over there fails this plan instead of a 8am job.
  batch          = jsondecode(data.aws_ssm_parameter.batch.insecure_value)
  job_queue_arn  = local.batch.job_queue_arn
  s3_bucket_name = local.batch.bucket

  # The ECR repository lives in the same stack (its `repos` module), so the
  # image comes from there too, rather than from an IMAGE_URL secret that had
  # to be kept equal to it by hand.
  image = "${local.batch.repo_urls[var.ecr_repository_name]}:${var.image_tag}"

  # ncaabb's `box_scores` command also pulls possessions/box scores, so it
  # stays its own command instead of going through the generic `games`
  # command that the rest use.
  #
  # nfl and ncaafb aren't here: their `games` pull is the first step of the
  # football chain below, and a second schedule for it would run it twice a
  # day.
  games_jobs = {
    mens    = ["box_scores", "mens", var.season_year]
    womens  = ["box_scores", "womens", var.season_year]
    nhl     = ["games", "nhl", var.season_year]
    wnba    = ["games", "wnba", var.wnba_season_year]
    ncaawvb = ["games", "ncaawvb", var.season_year]
  }

  # ncaawvb isn't here: ESPN carries no odds for college volleyball as of Sept 2026
  # and `odds_jobs` below turns every league in this list into one job per
  # horizon -- so adding it would buy three schedules, one of them hourly,
  # writing nothing but empty objects. See `_ODDS_LEAGUES` in
  # py-endgame-aws.
  odds_leagues = ["ncaabb", "nfl", "ncaafb", "nhl", "wnba"]

  # How far ahead each odds pull looks, and how often it runs.
  #
  # ESPN's scoreboard takes a range of days, so a horizon costs one request
  # per chunk rather than one per day -- which is what makes asking about
  # the whole season affordable at all. The three overlap on purpose,
  # because they answer different questions:
  #
  #   today   the only one that sees a line move in the hours before a
  #           game, so it's the one that runs hourly
  #   near    a fortnight out, once a day: a game gets a price the day it
  #           opens rather than the day it's played
  #   season  once a week, for lines posted months ahead -- most of the
  #           NFL's season is priced by September, and college football
  #           prices rivalry week and the bowls long before they're near
  #
  # `today` keeps the hours the single odds job has always run. The other
  # two go early, before it starts, so a day's first hourly snapshot has
  # the wider pulls behind it rather than racing them.
  odds_horizons = {
    today  = "cron(0 10-22 * * ? *)"
    near   = "cron(30 9 * * ? *)"
    season = "cron(0 8 ? * MON *)"
  }

  # One job per league per horizon. The horizon is in the job name (and in
  # the S3 key the job writes) because the three run on their own
  # schedules: two that landed in the same minute would otherwise write the
  # same object and one would quietly replace the other.
  odds_jobs = {
    for pair in setproduct(local.odds_leagues, keys(local.odds_horizons)) :
    "${pair[0]}-${pair[1]}" => {
      league  = pair[0]
      horizon = pair[1]
    }
  }

  # The football play-by-play pipeline, which is three commands that have to
  # run in order:
  #
  #   games                  writes seasons/{year}/{league}.pkl -- which games
  #                          exist and which are finished
  #   football_plays         reads that, pulls play-by-play for the finished
  #                          games it doesn't have yet, writes
  #                          plays/{league}/{year}/{week}.json.gz
  #   process_football_plays reads those, writes the parquet readers query
  #
  # Ordered by Batch's own `dependsOn` rather than by staggered schedules, so
  # a slow `games` delays the pull instead of racing it. See
  # modules/chained_jobs.
  #
  # Each league runs this chain twice over: hourly through the afternoon and
  # night (`football_intraday`), so a game's plays and EPA are in within the
  # hour of its final, and once at 8am (`football`), which also re-fetches
  # the last 36 hours of games (`--refresh_hours`). ESPN's feed for a game
  # that just ended can still be revised, and `football_plays` otherwise
  # never asks for a stored game again; 36 hours gives every game a morning
  # re-read after the hourly runs caught it. `--week` and `--refresh` stay
  # manual knobs, for trying a single week or re-fetching one ESPN revised.
  football_leagues = ["nfl", "ncaafb"]
}

# ------------------------------------------------------------------------------
# Batch Job Definition
# ------------------------------------------------------------------------------
# ------------------------------------------------------------------------------
# Scheduled Job Module(s)
# ------------------------------------------------------------------------------
module "daily_games" {
  source   = "./modules/scheduled_job"
  for_each = local.games_jobs

  job_name            = "daily-games-${each.key}"
  image               = local.image
  command             = each.value
  execution_role_arn  = aws_iam_role.batch_execution_role.arn
  job_role_arn        = aws_iam_role.batch_job_role.arn
  scheduler_role_arn  = aws_iam_role.scheduler_role.arn
  job_queue_arn       = local.job_queue_arn
  schedule_expression = var.schedule_expression
  schedule_timezone   = var.schedule_timezone
}

# The football chains. One per league, each three jobs deep.
module "football" {
  source   = "./modules/chained_jobs"
  for_each = toset(local.football_leagues)

  chain_name = "endgame-football-${each.key}"
  steps = [
    {
      name    = "daily-games-${each.key}"
      command = ["games", each.key, var.season_year]
    },
    {
      name    = "football-plays-${each.key}"
      command = ["football_plays", each.key, var.season_year, "--refresh_hours=36"]
    },
    {
      name    = "process-football-plays-${each.key}"
      command = ["process_football_plays", each.key, var.season_year]
    },
  ]

  image              = local.image
  execution_role_arn = aws_iam_role.batch_execution_role.arn
  job_role_arn       = aws_iam_role.batch_job_role.arn
  scheduler_role_arn = aws_iam_role.scheduler_role.arn
  job_queue_arn      = local.job_queue_arn
  # The same 8am the other daily pulls run at. Nothing downstream needs its
  # own time any more.
  schedule_expression = var.schedule_expression
  schedule_timezone   = var.schedule_timezone
  # On. Both chains have been started by hand and watched through: the
  # dependants wait in PENDING for their dependency rather than for a clock,
  # `football_plays` skips the games it already has, and
  # `process_football_plays` writes the week's parquet.
  schedule_enabled = true

  # `process_football_plays` reads parquet through pyarrow's S3FileSystem,
  # which is the AWS SDK for C++ rather than botocore and resolves the
  # bucket's region itself if nothing tells it. Saying it outright removes a
  # request and a way for the job to fail that nothing else here shares.
  environment_variables = [
    {
      name  = "AWS_REGION"
      value = var.aws_region
    },
  ]
}

# The same chains, hourly through the hours games end in, so a result's plays
# -- and cassandra's EPA after them -- don't wait for 8am. Their own job
# definitions and state machines, because the steps differ: no refresh, since
# re-fetching the day's games every hour is what the 8am run is for. A run
# with nothing new costs one schedule pull and a read per week.
module "football_intraday" {
  source   = "./modules/chained_jobs"
  for_each = toset(local.football_leagues)

  chain_name = "endgame-football-intraday-${each.key}"
  steps = [
    {
      name    = "intraday-games-${each.key}"
      command = ["games", each.key, var.season_year]
    },
    {
      name    = "intraday-football-plays-${each.key}"
      command = ["football_plays", each.key, var.season_year]
    },
    {
      name    = "intraday-process-football-plays-${each.key}"
      command = ["process_football_plays", each.key, var.season_year]
    },
  ]

  image               = local.image
  execution_role_arn  = aws_iam_role.batch_execution_role.arn
  job_role_arn        = aws_iam_role.batch_job_role.arn
  scheduler_role_arn  = aws_iam_role.scheduler_role.arn
  job_queue_arn       = local.job_queue_arn
  schedule_expression = var.football_intraday_schedule
  schedule_timezone   = var.schedule_timezone
  schedule_enabled    = true

  environment_variables = [
    {
      name  = "AWS_REGION"
      value = var.aws_region
    },
  ]
}

module "odds" {
  source   = "./modules/scheduled_job"
  for_each = local.odds_jobs

  job_name            = "odds-${each.key}"
  image               = local.image
  command             = ["odds", each.value.league, "--horizon", each.value.horizon]
  execution_role_arn  = aws_iam_role.batch_execution_role.arn
  job_role_arn        = aws_iam_role.batch_job_role.arn
  scheduler_role_arn  = aws_iam_role.scheduler_role.arn
  job_queue_arn       = local.job_queue_arn
  schedule_expression = local.odds_horizons[each.value.horizon]
  schedule_timezone   = var.schedule_timezone
}

# ------------------------------------------------------------------------------
# IAM Roles for Batch
# ------------------------------------------------------------------------------

# Execution Role (Agent/Docker daemon permissions, e.g. pulling images)
resource "aws_iam_role" "batch_execution_role" {
  name = "endgame-batch-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "batch_execution_policy" {
  role       = aws_iam_role.batch_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Job Role (Application code permissions)
resource "aws_iam_role" "batch_job_role" {
  name = "endgame-batch-job-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

# S3 Permissions for Job Role
resource "aws_iam_policy" "batch_job_s3_policy" {
  name        = "endgame-batch-job-s3-policy"
  description = "Policy allowing Batch Job to write to specific S3 bucket"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:PutObjectAcl",
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          "arn:aws:s3:::${local.s3_bucket_name}",
          "arn:aws:s3:::${local.s3_bucket_name}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "batch_job_s3_policy_attach" {
  role       = aws_iam_role.batch_job_role.name
  policy_arn = aws_iam_policy.batch_job_s3_policy.arn
}

# ------------------------------------------------------------------------------
# Data Lookups
# ------------------------------------------------------------------------------
# aws-batch-optimization, in this same account, publishes its non-sensitive
# outputs as one JSON parameter (its infra/ssm.tf) -- the queue, the bucket and
# the ECR repository URLs among them -- so this repo doesn't take them as
# variables at all, and doesn't need to know where that stack keeps its state.
data "aws_ssm_parameter" "batch" {
  name = var.shared_outputs_parameter
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}


# ------------------------------------------------------------------------------
# IAM Role for Scheduler
# ------------------------------------------------------------------------------
resource "aws_iam_role" "scheduler_role" {
  name = "endgame-scheduler-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "scheduler.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_policy" "scheduler_policy" {
  name        = "endgame-scheduler-policy"
  description = "Policy allowing EventBridge Scheduler to submit Batch jobs and start chains"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "batch:SubmitJob"
        Resource = [
          local.job_queue_arn,
          "arn:aws:batch:${var.aws_region}:${data.aws_caller_identity.current.account_id}:job-definition/*"
        ]
      },
      {
        Effect = "Allow"
        Action = "states:StartExecution"
        # By name pattern rather than by referencing the modules' outputs: the
        # chains take this role as an input, so reading their arns back here
        # would be a cycle.
        Resource = "arn:${data.aws_partition.current.partition}:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stateMachine:${var.resource_name_prefix}-*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "scheduler_policy_attach" {
  role       = aws_iam_role.scheduler_role.name
  policy_arn = aws_iam_policy.scheduler_policy.arn
}

# ------------------------------------------------------------------------------
# Chain failure notifications
# ------------------------------------------------------------------------------
# A job that fails is emailed by aws-batch-optimization (its alerts.tf), which
# watches the whole shared queue for every app. That doesn't cover a chain that
# never submitted a job -- a throttled SubmitJob, a permissions change --
# which would otherwise be a silently skipped day: no Batch job means no Batch
# event. So this rule sends chain failures to the same topic.
#
# Note a job failing mid-chain still notifies once per job, not once: Batch
# marks the jobs waiting on it FAILED too, and the shared rule matches each.
resource "aws_cloudwatch_event_rule" "chain_failure" {
  name        = "endgame-chain-failure-rule"
  description = "Trigger notification when a job chain fails to submit its jobs"

  event_pattern = jsonencode({
    source      = ["aws.states"]
    detail-type = ["Step Functions Execution Status Change"]
    detail = {
      status = ["FAILED", "TIMED_OUT", "ABORTED"]
      stateMachineArn = [
        for chain in concat(values(module.football), values(module.football_intraday)) :
        chain.state_machine_arn
      ]
    }
  })
}

resource "aws_cloudwatch_event_target" "chain_failure_sns" {
  rule      = aws_cloudwatch_event_rule.chain_failure.name
  target_id = "SendToSNS"
  arn       = local.batch.failure_topic_arn
}
