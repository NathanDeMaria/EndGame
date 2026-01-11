terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

resource "aws_batch_job_definition" "this" {
  name = var.job_name
  type = "container"

  # Container properties (JSON)
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

    command     = var.command
    environment = var.environment_variables

    # Execution role (needed to pull from ECR)
    executionRoleArn = var.execution_role_arn
    # Job role (needed for application permissions like S3)
    jobRoleArn = var.job_role_arn
  })

  platform_capabilities = ["EC2"]
}

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
    role_arn = var.scheduler_role_arn

    input = jsonencode({
      JobName       = "${var.job_name}-scheduled-run"
      JobQueue      = var.job_queue_arn
      JobDefinition = aws_batch_job_definition.this.arn
    })
  }
}
