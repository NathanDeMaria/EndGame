TF_ARGS=-var-file=terraform.tfvars

# Needs no credentials and no backend, so it runs anywhere -- including in CI
# before the OIDC roles are wired up, where it's the only signal this repo's
# terraform workflow gives.
lint: test
	terraform fmt -check -recursive
	terraform init -backend=false -input=false
	terraform validate

# The finals watcher's unit tests. Standard library only, so they need no
# install -- which is what lets the terraform workflow's lint job, the one CI
# job that sees a change to jobs/, run them.
test:
	cd finals_watcher && python3 -m unittest -v finals_test

plan:
	terraform plan $(TF_ARGS)


apply:
	terraform apply $(TF_ARGS)
