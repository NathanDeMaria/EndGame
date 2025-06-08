ifeq ($(OS),Windows_NT)
SHELL := powershell.exe
.SHELLFLAGS := -NoProfile -Command
endif

IMAGE_URL ?= $(shell cat .\config.json | jq -r .repo_urls.value.endgame)
ACCOUNT := $(shell aws sts get-caller-identity --query "Account" --output text)
REGION=us-east-2

build:
	docker build -f .devcontainer/Dockerfile -t ${IMAGE_URL} .

_ecr_login:
	aws ecr get-login-password --region ${REGION} | docker login --username AWS --password-stdin ${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com

push: build _ecr_login
	docker push ${IMAGE_URL}
