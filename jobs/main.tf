provider "aws" {
  region = var.aws_region
}

locals {
  # The account's Batch stack owns the job queue and the data bucket, and its
  # state is the source of truth for both. Reading them from there rather than
  # re-declaring them here means there's nothing to keep in sync by hand, and
  # a rename over there fails this plan instead of a 8am job.
  job_queue_arn  = data.terraform_remote_state.batch.outputs.job_queue_arn
  s3_bucket_name = data.terraform_remote_state.batch.outputs.bucket

  # ncaabb's `box_scores` command also pulls possessions/box scores, so it
  # stays its own command instead of going through the generic `games`
  # command that the rest use.
  #
  # nfl and ncaafb aren't here: their `games` pull is the first step of the
  # football chain below, and a second schedule for it would run it twice a
  # day.
  games_jobs = {
    mens   = ["box_scores", "mens", var.season_year]
    womens = ["box_scores", "womens", var.season_year]
    nhl    = ["games", "nhl", var.season_year]
    wnba   = ["games", "wnba", var.wnba_season_year]
  }

  odds_leagues = ["ncaabb", "nfl", "ncaafb", "nhl", "wnba"]

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
  # Neither football step is given `--week` or `--refresh`. Both commands
  # take them; they're the manual knobs, for trying a single week or
  # re-fetching one ESPN revised.
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
  image               = "${var.ecr_repository_url}:${var.image_tag}"
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
      command = ["football_plays", each.key, var.season_year]
    },
    {
      name    = "process-football-plays-${each.key}"
      command = ["process_football_plays", each.key, var.season_year]
    },
  ]

  image              = "${var.ecr_repository_url}:${var.image_tag}"
  execution_role_arn = aws_iam_role.batch_execution_role.arn
  job_role_arn       = aws_iam_role.batch_job_role.arn
  scheduler_role_arn = aws_iam_role.scheduler_role.arn
  job_queue_arn      = local.job_queue_arn
  # The same 8am the other daily pulls run at. Nothing downstream needs its
  # own time any more.
  schedule_expression = var.schedule_expression
  schedule_timezone   = var.schedule_timezone
  # Off until the chain has been started by hand and watched all the way
  # through. Flip to true to hand it to the scheduler.
  schedule_enabled = false

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

module "odds" {
  source   = "./modules/scheduled_job"
  for_each = toset(local.odds_leagues)

  job_name            = "odds-${each.key}"
  image               = "${var.ecr_repository_url}:${var.image_tag}"
  command             = ["odds", each.key]
  execution_role_arn  = aws_iam_role.batch_execution_role.arn
  job_role_arn        = aws_iam_role.batch_job_role.arn
  scheduler_role_arn  = aws_iam_role.scheduler_role.arn
  job_queue_arn       = local.job_queue_arn
  schedule_expression = "cron(0 10-22 * * ? *)"
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
# `batch-state` is the aws-batch-optimization stack, in this same account and
# state bucket. It already exports the queue and bucket, so this repo doesn't
# take them as variables at all.
data "terraform_remote_state" "batch" {
  backend = "s3"
  config = {
    bucket = "nathan-terraform"
    key    = "batch-state"
    region = "us-east-2"
  }
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
# Failure Notifications (SNS)
# ------------------------------------------------------------------------------
resource "aws_sns_topic" "batch_failure" {
  name = "endgame-batch-failure-topic"
}

resource "aws_sns_topic_subscription" "batch_failure_email" {
  topic_arn = aws_sns_topic.batch_failure.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

resource "aws_cloudwatch_event_rule" "batch_failure" {
  name        = "endgame-batch-failure-rule"
  description = "Trigger notification when Batch Job fails"

  event_pattern = jsonencode({
    source      = ["aws.batch"]
    detail-type = ["Batch Job State Change"]
    detail = {
      status   = ["FAILED"]
      jobQueue = [local.job_queue_arn]
    }
  })

}

resource "aws_cloudwatch_event_target" "sns" {
  rule      = aws_cloudwatch_event_rule.batch_failure.name
  target_id = "SendToSNS"
  arn       = aws_sns_topic.batch_failure.arn
  input_transformer {
    input_paths = {
      jobName = "$.detail.jobName"
      status  = "$.detail.status"
      reason  = "$.detail.statusReason"
      jobId   = "$.detail.jobId"
    }
    input_template = "\"Job <jobName> (ID: <jobId>) has <status>. Reason: <reason>\""
  }
}

resource "aws_sns_topic_policy" "default" {
  arn = aws_sns_topic.batch_failure.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowEventsToPublish"
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.batch_failure.arn
      }
    ]
  })
}

# ------------------------------------------------------------------------------
# Chain failure notifications
# ------------------------------------------------------------------------------
# `batch_failure` above covers a job that fails. It doesn't cover a chain that
# never submitted one -- a throttled SubmitJob, a permissions change -- which
# would otherwise be a silently skipped day: no Batch job means no Batch
# event.
#
# Note a job failing mid-chain still notifies once per job, not once: Batch
# marks the jobs waiting on it FAILED too, and `batch_failure` matches the
# whole queue.
resource "aws_cloudwatch_event_rule" "chain_failure" {
  name        = "endgame-chain-failure-rule"
  description = "Trigger notification when a job chain fails to submit its jobs"

  event_pattern = jsonencode({
    source      = ["aws.states"]
    detail-type = ["Step Functions Execution Status Change"]
    detail = {
      status          = ["FAILED", "TIMED_OUT", "ABORTED"]
      stateMachineArn = [for chain in module.football : chain.state_machine_arn]
    }
  })
}

resource "aws_cloudwatch_event_target" "chain_failure_sns" {
  rule      = aws_cloudwatch_event_rule.chain_failure.name
  target_id = "SendToSNS"
  arn       = aws_sns_topic.batch_failure.arn
}
