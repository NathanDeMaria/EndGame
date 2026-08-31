variable "chain_name" {
  description = "Name of the chain. Names the state machine and its schedule."
  type        = string
}

variable "steps" {
  description = <<-EOT
    The jobs to run, in order. Each gets its own Batch job definition, named
    by `name`, and each waits on the one before it.

    Order is the list order, and it's the whole contract: step N is submitted
    with a `dependsOn` pointing at step N-1's job id, so Batch holds it in
    PENDING until that job succeeds and fails it if that job fails.
  EOT
  type = list(object({
    name    = string
    command = list(string)
  }))

  validation {
    condition     = length(var.steps) > 0
    error_message = "A chain needs at least one step."
  }

  validation {
    condition     = length(distinct([for step in var.steps : step.name])) == length(var.steps)
    error_message = "Step names must be unique; they name the Batch job definitions."
  }
}

variable "image" {
  description = "The Docker image URL (e.g. repo_url:tag) every step runs"
  type        = string
}

variable "execution_role_arn" {
  description = "ARN of the IAM role for Batch execution"
  type        = string
}

variable "job_role_arn" {
  description = "ARN of the IAM role for the Batch Jobs"
  type        = string
}

variable "scheduler_role_arn" {
  description = "ARN of the IAM role EventBridge Scheduler assumes to start an execution"
  type        = string
}

variable "job_queue_arn" {
  description = "ARN of the Batch Job Queue every step is submitted to"
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

variable "schedule_enabled" {
  description = <<-EOT
    Whether the schedule fires.

    False creates it DISABLED, which is how a chain gets deployed and tested
    before it's trusted to run on its own: everything exists and can be
    started by hand, nothing fires at 8am. Flipping it to true is a
    one-attribute update, not a create, so the enabling apply changes only
    what it says on the tin.
  EOT
  type        = bool
  default     = true
}

variable "environment_variables" {
  description = "Environment variables passed to every step"
  type = list(object({
    name  = string
    value = string
  }))
  default = []
}
