output "state_machine_arn" {
  description = "The ARN of the state machine that submits the chain"
  value       = aws_sfn_state_machine.this.arn
}

output "job_definition_arns" {
  description = "The ARN of each step's job definition, keyed by step name"
  value       = { for name, definition in aws_batch_job_definition.step : name => definition.arn }
}
