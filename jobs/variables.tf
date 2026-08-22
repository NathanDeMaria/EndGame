variable "aws_region" {
  description = "AWS region to deploy resources into"
  type        = string
}

variable "batch_job_queue_name" {
  description = "The name of the AWS Batch Job Queue"
  type        = string
}

variable "ecr_repository_url" {
  description = "The URL of the ECR repository"
  type        = string
}

variable "image_tag" {
  description = "The tag of the container image to use"
  type        = string
  default     = "latest"
}

variable "season_year" {
  description = <<-EOT
    The season to pull, for the leagues whose season is named for the year it
    starts in: ncaabb, nfl, ncaafb and nhl. Bump it once a year, around
    August, when football and hockey start up and the previous ncaabb season
    is long finished.

    This used to be derived from `timestamp()`, which meant the deployed year
    depended on the day of the apply rather than on anything in git: the same
    commit planned in July and in August produced different job definitions,
    with no change in between. Committing the year keeps applies
    deterministic, which is what makes it safe for CI to apply on merge.
  EOT
  type        = string
  default     = "2026"
}

variable "wnba_season_year" {
  description = <<-EOT
    The WNBA season to pull. Separate from `season_year` because the WNBA is
    the one league whose season sits inside a single calendar year (May to
    October), so it rolls over in the spring rather than in August.
  EOT
  type        = string
  default     = "2026"
}

variable "schedule_expression" {
  description = "The cron expression for the schedule"
  type        = string
  default     = "cron(0 8 * * ? *)"
}

variable "schedule_timezone" {
  description = "The timezone for the schedule"
  type        = string
  default     = "America/Chicago"
}

variable "s3_bucket_name" {
  description = "The name of the S3 bucket to write to"
  type        = string
}

variable "notification_email" {
  description = "Email address to receive notifications when the job fails"
  type        = string
}
