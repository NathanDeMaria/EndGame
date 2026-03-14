# 3.14-bookworm on 2026-03-13
FROM python@sha256:6cc50e409fb008d64c8c119c33488cf0e2288fb657a9ceeae237d381f842713e

# For persisting bash history
RUN SNIPPET="export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" && echo $SNIPPET >> "/root/.bashrc"

# Install Poetry using the recommended installer
ENV POETRY_HOME="/opt/poetry" \
    POETRY_VERSION=2.3.2
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="${POETRY_HOME}/bin:$PATH"

# Is this a good way to add stuff on load?
# Not sure, but it's working
COPY profile.sh /root/profile.sh
RUN echo ". profile.sh" >> "/root/.bashrc"

COPY poetry.* ./
COPY pyproject.toml ./
