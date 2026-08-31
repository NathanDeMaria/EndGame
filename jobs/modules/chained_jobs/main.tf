terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

# ------------------------------------------------------------------------------
# One job definition per step
# ------------------------------------------------------------------------------
# Same shape as `scheduled_job`'s, minus the schedule: a step doesn't have one
# of its own. The chain is what runs it.
resource "aws_batch_job_definition" "step" {
  for_each = { for step in var.steps : step.name => step }

  name = each.key
  type = "container"

  container_properties = jsonencode({
    image = var.image

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

    command     = each.value.command
    environment = var.environment_variables

    executionRoleArn = var.execution_role_arn
    jobRoleArn       = var.job_role_arn
  })

  platform_capabilities = ["EC2"]
}

# ------------------------------------------------------------------------------
# The chain
# ------------------------------------------------------------------------------
locals {
  state_names = [for index, step in var.steps : "Submit-${step.name}"]

  # `$.step_0`, `$.step_1`, ... hold each submitted job's id. Indices rather
  # than the step names because these are JSONPath keys and a step name has
  # hyphens in it.
  result_paths = [for index, step in var.steps : "$.step_${index}"]

  states = {
    for index, step in var.steps : local.state_names[index] => merge(
      {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:batch:submitJob"

        Parameters = merge(
          {
            JobName       = "${step.name}-chained-run"
            JobQueue      = var.job_queue_arn
            JobDefinition = aws_batch_job_definition.step[step.name].arn
          },
          # The dependency itself. Batch holds this job in PENDING -- using no
          # compute -- until the previous one succeeds, and fails it outright
          # if that one fails. The first step depends on nothing.
          index == 0 ? {} : {
            DependsOn = [
              { "JobId.$" = "${local.result_paths[index - 1]}.JobId" }
            ]
          }
        )

        # Keep only the job id. The rest of SubmitJob's response is the job's
        # name and arn, which the next step doesn't need.
        ResultSelector = { "JobId.$" = "$.JobId" }
        ResultPath     = local.result_paths[index]
      },
      index == length(var.steps) - 1
      ? { End = true }
      : { Next = local.state_names[index + 1] }
    )
  }
}

resource "aws_sfn_state_machine" "this" {
  name     = var.chain_name
  role_arn = aws_iam_role.state_machine.arn

  definition = jsonencode({
    Comment = "Submit ${var.chain_name}'s jobs, each depending on the one before it"
    StartAt = local.state_names[0]
    States  = local.states
  })
}

# ------------------------------------------------------------------------------
# IAM for the state machine
# ------------------------------------------------------------------------------
resource "aws_iam_role" "state_machine" {
  name = "${var.chain_name}-state-machine-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_policy" "state_machine" {
  name        = "${var.chain_name}-state-machine-policy"
  description = "Policy allowing the ${var.chain_name} state machine to submit its jobs"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "batch:SubmitJob"
        # Scoped to this chain's own job definitions rather than the account's
        # -- the state machine submits exactly these and nothing else.
        # Revision-qualified, and a change to a step registers a new revision
        # and updates this in the same apply.
        Resource = concat(
          [var.job_queue_arn],
          [for definition in aws_batch_job_definition.step : definition.arn]
        )
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "state_machine" {
  role       = aws_iam_role.state_machine.name
  policy_arn = aws_iam_policy.state_machine.arn
}

# ------------------------------------------------------------------------------
# Schedule
# ------------------------------------------------------------------------------
# Starts an execution rather than submitting a job. The execution finishes in
# about a second: all it does is submit the chain's jobs, which then run --
# and wait on each other -- in Batch.
resource "aws_scheduler_schedule" "this" {
  name       = "${var.chain_name}-schedule"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  state                        = var.schedule_enabled ? "ENABLED" : "DISABLED"
  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = var.schedule_timezone

  target {
    arn      = aws_sfn_state_machine.this.arn
    role_arn = var.scheduler_role_arn

    input = jsonencode({})
  }
}
