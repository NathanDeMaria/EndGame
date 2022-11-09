# 3.11.0-bullseye on 2022-10-30
FROM python@sha256:3e2e743ca3027c375a13ee7b9b0f7757b0091501a7556563afbc8000b5c862a5

# For persisting bash history
RUN SNIPPET="export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" && echo $SNIPPET >> "/root/.bashrc"

# Poetry
RUN curl -sSL https://install.python-poetry.org | python3 - --version 1.2.2
ENV PATH /root/.local/bin:${PATH}

# Is this a good way to add stuff on load?
# Not sure, but it's working
COPY profile.sh /root/profile.sh
RUN echo ". profile.sh" >> "/root/.bashrc"

COPY poetry.* ./
COPY pyproject.toml ./
RUN poetry install

RUN endgame -- --completion bash > ~/.endgame-completion.sh && echo ". ~/.endgame-completion.sh" > "/root/.bashrc"

# Copying everything outside of the workdir, for use as a deployed Docker image
ADD . /root/runtime

ENTRYPOINT ["python", "/root/runtime/main.py"]
CMD ["--", "--help"]
