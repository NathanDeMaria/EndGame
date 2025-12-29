provider "aws" {
  region = var.aws_region
}

# ------------------------------------------------------------------------------
# Batch Job Definition
# ------------------------------------------------------------------------------
resource "aws_batch_job_definition" "this" {
  name = var.job_name
  type = "container"

  # Container properties (JSON)
  container_properties = jsonencode({
    image = "${var.ecr_repository_url}:${var.image_tag}"

    resourceRequirements = [
      {
        type  = "VCPU"
        value = "2"
      },
      {
        type  = "MEMORY"
        value = "4096"
      }
    ]

    command = ["odds"]

    # Execution role (needed to pull from ECR)
    executionRoleArn = aws_iam_role.batch_execution_role.arn
    # Job role (needed for application permissions like S3)
    jobRoleArn = aws_iam_role.batch_job_role.arn
  })

  platform_capabilities = ["EC2"]
}

# ------------------------------------------------------------------------------
# IAM Roles for Batch
# ------------------------------------------------------------------------------

# Execution Role (Agent/Docker daemon permissions, e.g. pulling images)
resource "aws_iam_role" "batch_execution_role" {
  name = "${var.job_name}-execution-role"

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
  name = "${var.job_name}-job-role"

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
  name        = "${var.job_name}-s3-policy"
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
  name = "${var.job_name}-scheduler-role"

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
  name        = "${var.job_name}-scheduler-policy"
  description = "Policy allowing EventBridge Scheduler to submit Batch jobs"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "batch:SubmitJob"
        Resource = [
          data.aws_batch_job_queue.this.arn,
          aws_batch_job_definition.this.arn,
          "arn:aws:batch:${var.aws_region}::job-definition/*"
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
# EventBridge Scheduler
# ------------------------------------------------------------------------------
resource "aws_scheduler_schedule" "this" {
  name       = "${var.job_name}-schedule"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = var.schedule_timezone

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:batch:submitJob"
    role_arn = aws_iam_role.scheduler_role.arn

    input = jsonencode({
      JobName       = "${var.job_name}-scheduled-run"
      JobQueue      = data.aws_batch_job_queue.this.arn
      JobDefinition = aws_batch_job_definition.this.arn
    })
  }
}

# ------------------------------------------------------------------------------
# Failure Notifications (SNS)
# ------------------------------------------------------------------------------
resource "aws_sns_topic" "batch_failure" {
  name = "${var.job_name}-failure-topic"
}

resource "aws_sns_topic_subscription" "batch_failure_email" {
  topic_arn = aws_sns_topic.batch_failure.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

resource "aws_cloudwatch_event_rule" "batch_failure" {
  name        = "${var.job_name}-failure-rule"
  description = "Trigger notification when Batch Job fails"

  event_pattern = jsonencode({
    source      = ["aws.batch"]
    detail-type = ["Batch Job State Change"]
    detail = {
      status   = ["FAILED"]
      jobQueue = [data.aws_batch_job_queue.this.arn]
      jobDefinition = [{
        prefix = "arn:aws:batch:${var.aws_region}:${data.aws_caller_identity.current.account_id}:job-definition/${aws_batch_job_definition.this.name}"
      }]
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
