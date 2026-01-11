output "job_definition_arn" {
  description = "The ARN of the created Batch Job Definition"
  value       = aws_batch_job_definition.this.arn
}

output "job_definition_name" {
  description = "The name of the created Batch Job Definition"
  value       = aws_batch_job_definition.this.name
}
