variable "job_name" {
  description = "The name of the Batch Job"
  type        = string
}

variable "image" {
  description = "The comprehensive Docker image URL (e.g. repo_url:tag)"
  type        = string
}

variable "command" {
  description = "The command to run in the container"
  type        = list(string)
}

variable "execution_role_arn" {
  description = "ARN of the IAM role for Batch execution"
  type        = string
}

variable "job_role_arn" {
  description = "ARN of the IAM role for the Batch Job"
  type        = string
}

variable "scheduler_role_arn" {
  description = "ARN of the IAM role for the Scheduler"
  type        = string
}

variable "job_queue_arn" {
  description = "ARN of the Batch Job Queue"
  type        = string
}

variable "schedule_expression" {
  description = "The cron expression for the schedule"
  type        = string
}

variable "schedule_timezone" {
  description = "The timezone for the schedule"
  type        = string
}

variable "environment_variables" {
  description = "List of environment variables to pass to the container"
  type = list(object({
    name  = string
    value = string
  }))
  default = []
}
