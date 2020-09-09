FROM python:3.8

# For persisting bash history
RUN SNIPPET="export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" && echo $SNIPPET >> "/root/.bashrc"

RUN curl -sSL https://raw.githubusercontent.com/python-poetry/poetry/1.0.10/get-poetry.py | python
ENV PATH /root/.poetry/bin:${PATH}

COPY pyproject.toml ./
COPY poetry.* ./
RUN poetry install
