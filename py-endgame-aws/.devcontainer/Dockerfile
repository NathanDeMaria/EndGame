# 3.13.4-bullseye on 2025-06-08
FROM python@sha256:0cfd98c036493924a8f80c343aeb1d14a23e8ef4c8c18c7932c36c80fa2e72aa AS base

# Poetry gets its own venv so its dependencies can't collide with the app's:
# poetry.toml sets virtualenvs.create = false, so app deps land in the system
# site-packages. This is what install.python-poetry.org does, minus the curl.
ENV POETRY_HOME="/opt/poetry" \
    POETRY_VERSION=2.1.3
RUN python3 -m venv "$POETRY_HOME" \
    && "$POETRY_HOME/bin/pip" install --no-cache-dir "poetry==$POETRY_VERSION" \
    && ln -s "$POETRY_HOME/bin/poetry" /usr/local/bin/poetry
# NB: only `poetry` itself is linked onto PATH, deliberately. Putting
# $POETRY_HOME/bin on PATH would shadow `python`/`pip` with the poetry venv's
# copies, and ENTRYPOINT runs a bare `python` -- it must stay the system one
# that `poetry install` populated.

COPY poetry.* ./
COPY pyproject.toml ./
RUN poetry install --no-root --only main


# What actually gets deployed. Kept free of the dev tooling below so CI never
# pays for the awscli install, and so the pushed image stays small.
FROM base AS runtime

# Copying everything outside of the workdir, for use as a deployed Docker image
ADD . /root/runtime

ENTRYPOINT ["python", "/root/runtime/main.py"]
CMD ["--", "--help"]


# The devcontainer target. Branches off `base` rather than `runtime` so that
# editing source doesn't invalidate the awscli layer on a local rebuild.
FROM base AS dev

RUN poetry install --no-root

# Only needed for interactive use (`aws sso login`, `make push`) -- nothing in
# endgame_aws shells out to the CLI, it goes through aiobotocore.
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" &&\
    unzip awscliv2.zip &&\
    ./aws/install &&\
    rm awscliv2.zip &&\
    rm -r ./aws

# For persisting bash history
RUN SNIPPET="export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" && echo $SNIPPET >> "/root/.bashrc"

# Is this a good way to add stuff on load?
# Not sure, but it's working
COPY profile.sh /root/profile.sh
RUN echo ". /root/profile.sh" >> "/root/.bashrc"

RUN endgame -- --completion bash > ~/.endgame-completion.sh && echo ". ~/.endgame-completion.sh" >> "/root/.bashrc"
