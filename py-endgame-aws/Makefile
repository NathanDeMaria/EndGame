ifeq ($(OS),Windows_NT)
SHELL := powershell.exe
.SHELLFLAGS := -NoProfile -Command
endif

IMAGE_URL ?= $(shell cat .\config.json | jq -r .repo_urls.value.endgame)
ACCOUNT := $(shell aws sts get-caller-identity --query "Account" --output text)
REGION=us-east-2
TAG ?= local

# Get the current git commit hash
GIT_COMMIT := $(shell git rev-parse --short HEAD)

# Check if we're on the main branch
IS_MAIN := $(shell git rev-parse --abbrev-ref HEAD | grep -q ^main$$ && echo true || echo false)

# CI passes buildx cache flags in here; empty locally, where the daemon's own
# layer cache already does the job.
CACHE_FLAGS ?=

# Everything about the build that doesn't depend on where the result goes.
# `build` and `push` differ only in their output flag, so they stay one build
# definition -- tagging included, rather than a follow-up `docker tag`.
BUILD_FLAGS := --target runtime -f .devcontainer/Dockerfile -t ${IMAGE_URL}:${TAG}
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
