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

build:
	docker build -f .devcontainer/Dockerfile -t ${IMAGE_URL}:${TAG} .
	@if [ "${IS_MAIN}" = "true" ]; then \
		docker tag ${IMAGE_URL}:${TAG} ${IMAGE_URL}:latest; \
	fi

_ecr_login:
	aws ecr get-login-password --region ${REGION} | docker login --username AWS --password-stdin ${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com

push: build _ecr_login
	docker push ${IMAGE_URL}:${TAG}
	@if [ "${IS_MAIN}" = "true" ]; then \
		docker push ${IMAGE_URL}:latest; \
	fi
