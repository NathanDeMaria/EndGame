TF_ARGS=-var-file=terraform.tfvars

# Needs no credentials and no backend, so it runs anywhere -- including in CI
# before the OIDC roles are wired up, where it's the only signal this repo's
# terraform workflow gives.
lint:
	terraform fmt -check -recursive
	terraform init -backend=false -input=false
	terraform validate

plan:
	terraform plan $(TF_ARGS)


apply:
	terraform apply $(TF_ARGS)
