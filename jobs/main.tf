provider "aws" {
  region = var.aws_region
}

locals {
  current_month = tonumber(formatdate("M", timestamp()))
  current_year  = tonumber(formatdate("YYYY", timestamp()))
  # If before August (8), use previous year as season year.
  # TODO: will need to pass in the year in the future
  # this locks on the date of deploy
  season_year = local.current_month < 8 ? tostring(local.current_year - 1) : tostring(local.current_year)
}

# ------------------------------------------------------------------------------
# Batch Job Definition
# ------------------------------------------------------------------------------
# ------------------------------------------------------------------------------
# Scheduled Job Module(s)
# ------------------------------------------------------------------------------
module "odds" {
  source = "./modules/scheduled_job"

  job_name            = "odds"
  image               = "${var.ecr_repository_url}:${var.image_tag}"
  command             = ["odds"]
  execution_role_arn  = aws_iam_role.batch_execution_role.arn
  job_role_arn        = aws_iam_role.batch_job_role.arn
  scheduler_role_arn  = aws_iam_role.scheduler_role.arn
  job_queue_arn       = data.aws_batch_job_queue.this.arn
  schedule_expression = "cron(0 10-22 * * ? *)"
  schedule_timezone   = var.schedule_timezone
}

module "daily_games_mens" {
  source = "./modules/scheduled_job"

  job_name            = "daily-games-mens"
  image               = "${var.ecr_repository_url}:${var.image_tag}"
  command             = ["box_scores", "mens", local.season_year]
  execution_role_arn  = aws_iam_role.batch_execution_role.arn
  job_role_arn        = aws_iam_role.batch_job_role.arn
  scheduler_role_arn  = aws_iam_role.scheduler_role.arn
  job_queue_arn       = data.aws_batch_job_queue.this.arn
  schedule_expression = var.schedule_expression
  schedule_timezone   = var.schedule_timezone
}

module "daily_games_womens" {
  source = "./modules/scheduled_job"

  job_name            = "daily-games-womens"
  image               = "${var.ecr_repository_url}:${var.image_tag}"
  command             = ["box_scores", "womens", local.season_year]
  execution_role_arn  = aws_iam_role.batch_execution_role.arn
  job_role_arn        = aws_iam_role.batch_job_role.arn
  scheduler_role_arn  = aws_iam_role.scheduler_role.arn
  job_queue_arn       = data.aws_batch_job_queue.this.arn
  schedule_expression = var.schedule_expression
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
          "arn:aws:s3:::${var.s3_bucket_name}",
          "arn:aws:s3:::${var.s3_bucket_name}/*"
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
data "aws_batch_job_queue" "this" {
  name = var.batch_job_queue_name
}

data "aws_caller_identity" "current" {}


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
  description = "Policy allowing EventBridge Scheduler to submit Batch jobs"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "batch:SubmitJob"
        Resource = [
          data.aws_batch_job_queue.this.arn,
          "arn:aws:batch:${var.aws_region}:${data.aws_caller_identity.current.account_id}:job-definition/*"
        ]
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
      jobQueue = [data.aws_batch_job_queue.this.arn]
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
