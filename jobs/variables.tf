variable "aws_region" {
  description = "AWS region to deploy resources into"
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

variable "notification_email" {
  description = "Email address to receive notifications when the job fails"
  type        = string
}

# ------------------------------------------------------------------------------
# CI OIDC roles (oidc.tf)
# ------------------------------------------------------------------------------

variable "github_repository" {
  description = "owner/repo allowed to assume the CI roles"
  type        = string
  default     = "NathanDeMaria/EndGame"
}

variable "github_owner_id" {
  description = <<-EOT
    Numeric ID of the GitHub account owning the repository.

    GitHub issues OIDC subjects in an immutable, ID-qualified form --
    `repo:OWNER@OWNER_ID/REPO@REPO_ID:...` -- rather than by name, so the trust
    policy has to match on IDs. Matching on names alone silently never matches
    and every assume fails with a generic "Not authorized".

      gh api users/NathanDeMaria --jq .id
  EOT
  type        = number
  default     = 5595197
}

variable "github_repository_id" {
  description = <<-EOT
    Numeric ID of the repository. See github_owner_id.

      gh api repos/NathanDeMaria/EndGame --jq .id
  EOT
  type        = number
  default     = 125161304
}

variable "create_oidc_provider" {
  description = <<-EOT
    Create the GitHub Actions OIDC provider. Defaults false, like
    aws-batch-optimization: IAM permits exactly one provider per URL per
    account, and invisible-string creates the one in this account. If this
    account ever has none, set this true here and false there.
  EOT
  type        = bool
  default     = false
}

variable "state_bucket" {
  description = "Bucket holding terraform state. Plan needs write access for the lock file."
  type        = string
  default     = "nathan-terraform"
}

variable "state_key_prefix" {
  description = <<-EOT
    Key prefix within the state bucket that CI may lock and write. Matches the
    `key` in versions.tf; the trailing `*` in the policy covers the `.tflock`
    object `use_lockfile` writes beside it.
  EOT
  type        = string
  default     = "jobs/terraform.tfstate"
}

variable "resource_name_prefix" {
  description = "Prefix for the IAM this stack creates. Scopes the apply role's IAM permissions."
  type        = string
  default     = "endgame"
}
