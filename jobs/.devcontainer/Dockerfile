# :22.04
FROM ubuntu@sha256:104ae83764a5119017b8e8d6218fa0832b09df65aae7d5a6de29a85d813da2fb

ARG TERRAFORM_VERSION=1.14.3
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    bash \
    curl \
    git \
    openssh-client \
    ca-certificates \
    jq \
    unzip \
    python3 \
    python3-pip \
    less \
    wget \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip \
    -o terraform.zip \
 && unzip terraform.zip \
 && mv terraform /usr/local/bin/terraform \
 && rm terraform.zip

WORKDIR /workspace
ENTRYPOINT ["/bin/bash"]
