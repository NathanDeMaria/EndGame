output "state_machine_arn" {
  description = "The ARN of the state machine that submits the chain"
  value       = aws_sfn_state_machine.this.arn
}

output "job_definition_arns" {
  description = "The ARN of each step's job definition, keyed by step name"
  value       = { for name, definition in aws_batch_job_definition.step : name => definition.arn }
}

output "job_names" {
  description = <<-EOT
    The name each step's job is submitted under, in step order. What a watcher
    looks for in the queue to tell whether this chain is still running: the
    execution itself is over in a second, long before its jobs are.
  EOT
  value       = [for step in var.steps : "${step.name}-chained-run"]
}
