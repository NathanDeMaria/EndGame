# This repo's own make targets. The Makefile beside this one belongs to the
# mirrorball template and includes this file; this one is never touched by
# `copier update`.

# ------------------------------------------------------------------------------
# The Batch image (./Dockerfile)
# ------------------------------------------------------------------------------
# The terraform output from aws-batch-optimization (`make outputs` there). A
# local copy wins so CI can drop one in from the shared state.
CONFIG := $(firstword $(wildcard config.json $(HOME)/.aws-batch/config.json))
IMAGE_URL ?= $(shell jq -r .repo_urls.value.endgame $(CONFIG))
# Lazy, not `:=`: an immediate assignment calls STS on every make invocation,
# including `make build`, which needs no credentials.
ACCOUNT = $(shell aws sts get-caller-identity --query "Account" --output text)
REGION = us-east-2
TAG ?= local

# Check if we're on the main branch
IS_MAIN := $(shell git rev-parse --abbrev-ref HEAD | grep -q ^main$$ && echo true || echo false)

# CI passes buildx cache flags in here; empty locally, where the daemon's own
# layer cache already does the job.
CACHE_FLAGS ?=

# Everything about the build that doesn't depend on where the result goes.
# `build` and `push` differ only in their output flag, so they stay one build
# definition -- tagging included, rather than a follow-up `docker tag`.
BUILD_FLAGS := -t ${IMAGE_URL}:${TAG}
ifeq ($(IS_MAIN),true)
BUILD_FLAGS += -t ${IMAGE_URL}:latest
endif

build:
	docker buildx build ${CACHE_FLAGS} ${BUILD_FLAGS} --load .

_ecr_login:
	aws ecr get-login-password --region ${REGION} | docker login --username AWS --password-stdin ${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com

# `--push` uploads straight from the builder. Going through `build` would mean
# `--load`ing the image into the local daemon first -- a full tarball export
# and re-import (~35s in CI) whose only purpose was to give `docker push`
# something to read. Needs the ECR login first, both for the push itself and
# for the registry cache CI passes in CACHE_FLAGS.
push: _ecr_login
	docker buildx build ${CACHE_FLAGS} ${BUILD_FLAGS} --push .


.PHONY: build _ecr_login push
