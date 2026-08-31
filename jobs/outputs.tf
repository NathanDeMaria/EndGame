# Set these as GitHub Actions repository *variables* (not secrets -- a role ARN
# isn't secret, and terraform.yml compares them against '' to stay dormant
# until they exist):
#
#   gh variable set AWS_PLAN_ROLE_ARN  --body "$(terraform output -raw ci_plan_role_arn)"
#   gh variable set AWS_APPLY_ROLE_ARN --body "$(terraform output -raw ci_apply_role_arn)"

output "ci_plan_role_arn" {
  description = "role-to-assume for plan jobs (any branch, any PR)"
  value       = aws_iam_role.ci_plan.arn
}

output "ci_apply_role_arn" {
  description = "role-to-assume for apply jobs (main only)"
  value       = aws_iam_role.ci_apply.arn
}

# The chains, to start one by hand:
#
#   aws stepfunctions start-execution --state-machine-arn "$(terraform output -raw ...)"
output "football_state_machine_arns" {
  description = "State machine ARN per football league"
  value       = { for league, chain in module.football : league => chain.state_machine_arn }
}
