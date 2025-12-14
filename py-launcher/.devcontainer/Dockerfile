# 3.13.4-bullseye on 2025-06-08
FROM python@sha256:0cfd98c036493924a8f80c343aeb1d14a23e8ef4c8c18c7932c36c80fa2e72aa

ENV POETRY_VERSION=2.1.3 \
    POETRY_HOME=/opt/poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="${POETRY_HOME}/bin:${PATH}"

RUN mkdir -p /commandhistory && touch /commandhistory/.bash_history
ENV HISTFILE=/commandhistory/.bash_history

COPY poetry.* ./
COPY pyproject.toml ./
COPY README.md ./
COPY endgame_launcher/__init__.py ./endgame_launcher/
RUN poetry install
