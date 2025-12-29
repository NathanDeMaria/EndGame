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

variable "job_name" {
  description = "The name of the Batch Job"
  type        = string
  default     = "morning-batch-job"
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
