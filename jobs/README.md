# jobs

Terraform for the scheduled pulls: a Batch job definition and an EventBridge
schedule per league, the IAM they need, and SNS failure notifications.

The job queue and the data bucket aren't declared here. They belong to the
shared Batch stack ([aws-batch-optimization][abo]) and are read out of its
state with a `terraform_remote_state` data source, so a rename over there fails
this plan rather than an 8am job.

[abo]: https://github.com/NathanDeMaria/aws-batch-optimization/tree/main/infra

## CI

`terraform` runs in GitHub Actions ([`.github/workflows/terraform.yml`][wf]),
the same pattern as [invisible-string][is] and the Batch stack:

| Job | When | Credentials |
| --- | --- | --- |
| `lint` | every push and PR touching `jobs/**` | none |
| `plan` | every branch and PR except main | `AWS_PLAN_ROLE_ARN` |
| `apply` | push to main | `AWS_APPLY_ROLE_ARN` |

[wf]: ../.github/workflows/terraform.yml
[is]: https://github.com/NathanDeMaria/invisible-string/tree/main/infra

### Why two roles

`terraform plan` executes provider code and runs on every branch and PR. It
must not be able to reach credentials that can change anything. So:

- **plan role** — `ReadOnlyAccess`, plus write on this stack's state object and
  its lock file. Trusts `ref:refs/heads/*` *and* `pull_request`, because a PR's
  OIDC subject carries no ref at all — a policy trusting only refs fails on
  every PR.
- **apply role** — `PowerUserAccess` (everything but IAM) plus IAM scoped to
  `endgame-*`, the prefix every role and policy here is named with. Trusts
  `refs/heads/main` literally, so a branch named `main-hotfix` can't match.

Each repo's stack creates the roles its own workflow assumes; nothing is shared
across repos. `create_oidc_provider` defaults to **false** here, because IAM
permits one OIDC provider per URL per account and invisible-string creates the
one in this account.

### Setup

The roles are created by this stack, so the first apply is from a laptop.

```bash
make apply                 # creates endgame-ci-plan and endgame-ci-apply
gh variable set AWS_PLAN_ROLE_ARN  --body "$(terraform output -raw ci_plan_role_arn)"
gh variable set AWS_APPLY_ROLE_ARN --body "$(terraform output -raw ci_apply_role_arn)"
```

Repository **variables**, not secrets — a role ARN isn't secret, and the
workflow compares them against `''` to stay dormant until they're set. Before
that, `lint` is the only job that runs; `plan` and `apply` skip rather than
fail red.

CI also needs two repository **secrets**, which the docker workflow already
uses: `IMAGE_URL` (the untagged ECR repository URL) and `NOTIFICATION_EMAIL`.

## Local use

```bash
make plan
make apply
make lint         # what CI runs; no credentials needed
```

`make plan` and `make apply` read `terraform.tfvars`, which is gitignored. CI
passes the same values as `TF_VAR_*` instead.

## Bumping the season

`season_year` and `wnba_season_year` in `variables.tf` are committed rather
than derived from `timestamp()`, so the same commit plans the same way in July
and in August. Bump `season_year` around August, when football and hockey start
and the previous ncaabb season is long finished; `wnba_season_year` rolls over
in the spring.
