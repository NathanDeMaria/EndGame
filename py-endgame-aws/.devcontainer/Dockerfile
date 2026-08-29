# 3.14.7-trixie on 2026-08-29
FROM python@sha256:48651f00145ad01e9f83d468c57cec40fac72081950f9730205b87abc6087552 AS base

# Poetry gets its own venv so its dependencies can't collide with the app's:
# poetry.toml sets virtualenvs.create = false, so app deps land in the system
# site-packages. This is what install.python-poetry.org does, minus the curl.
# Root-owned deliberately -- `dev` below only ever executes poetry, it never
# installs into it.
ENV POETRY_HOME="/opt/poetry" \
    POETRY_VERSION=2.4.2
RUN python3 -m venv "$POETRY_HOME" \
    && "$POETRY_HOME/bin/pip" install --no-cache-dir "poetry==$POETRY_VERSION" \
    && ln -s "$POETRY_HOME/bin/poetry" /usr/local/bin/poetry
# NB: only `poetry` itself is linked onto PATH, deliberately. Putting
# $POETRY_HOME/bin on PATH would shadow `python`/`pip` with the poetry venv's
# copies, and ENTRYPOINT runs a bare `python` -- it must stay the system one
# that `poetry install` populated.

# Non-root, uid/gid 1000 to match the host user whose files get bind-mounted in
# (~/.aws, ~/.claude, ~/.endgame). Running as root here meant every SSO token
# refresh clobbered the host's cache with root-owned files. The runtime stage
# inherits the user rather than switching back: it has nothing to do as root,
# and one user across every stage means ~/.endgame/cache and
# ~/.aws-batch/config.json resolve to the same paths in a Batch job as on a
# laptop.
#
# The chown is because poetry.toml turns virtualenvs off, so /usr/local *is*
# the environment. Three paths under it have to be writable by dev:
#   site-packages  installed packages
#   bin            console scripts (`endgame`, `endgame-aws`)
#   src            git checkouts. Poetry clones VCS deps -- `endgame` is one --
#                  into `env.path / "src"`, NOT into the cache dir, so with no
#                  virtualenv that resolves to /usr/local/src, and a root-owned
#                  one fails the install with EPERM.
# The rest of the stdlib stays root-owned; chowning it would copy ~50M into
# this layer for nothing. Here rather than after the install below so the chown
# walks an empty site-packages.
#
# site-packages is looked up rather than spelled out, so this survives the
# python bump the pinned digest above is going to get eventually.
RUN apt-get update && apt-get install -y --no-install-recommends sudo \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 dev \
    && useradd --uid 1000 --gid 1000 -m -s /bin/bash dev \
    && echo "dev ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/dev \
    && chmod 0440 /etc/sudoers.d/dev \
    && mkdir -p /usr/local/src \
    && chown -R dev:dev \
        "$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')" \
        /usr/local/bin \
        /usr/local/src

USER dev
WORKDIR /home/dev

# Parents of the bind mounts in devcontainer.json. Docker creates a missing
# mount point as root, and chowning on the host can't fix these because the
# parents themselves aren't mounted -- so `aws configure` and anything else
# writing a new file straight into ~/.aws would hit EPERM. Pre-creating them as
# dev means docker finds them already there and leaves ownership alone.
# ~/.endgame is also where a Batch job writes its game cache.
RUN mkdir -p /home/dev/.aws /home/dev/.aws-batch /home/dev/.endgame

# Claude Code and anything else installed per-user lands here.
ENV PATH="/home/dev/.local/bin:${PATH}"

COPY --chown=dev:dev poetry.* ./
COPY --chown=dev:dev pyproject.toml ./
RUN poetry install --no-root --only main


# What actually gets deployed. Kept free of the dev tooling below so CI never
# pays for the awscli install, and so the pushed image stays small.
FROM base AS runtime

# Copying everything outside of the workdir, for use as a deployed Docker image
ADD --chown=dev:dev . /home/dev/runtime

ENTRYPOINT ["python", "/home/dev/runtime/main.py"]
CMD ["--", "--help"]


# The devcontainer target. Branches off `base` rather than `runtime` so that
# editing source doesn't invalidate the awscli layer on a local rebuild.
FROM base AS dev

RUN poetry install --no-root

# The installers that write outside $HOME. `sudo` rather than a `USER root` /
# `USER dev` sandwich so the layer's files land owned by whoever should own
# them without a follow-up chown.
#
# Only needed for interactive use (`aws sso login`, `make push`) -- nothing in
# endgame_aws shells out to the CLI, it goes through aiobotocore.
#
# `uname -m` because that is how the AWS CLI spells the arch in its download
# urls (x86_64 / aarch64) -- `awscli-exe-linux-amd64.zip` is a 404. Not the
# `dpkg --print-architecture` used for gh below, which spells the same two
# machines amd64 / arm64 because that is what Debian package names use.
RUN arch="$(uname -m)" \
    && curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${arch}.zip" -o awscliv2.zip \
    && unzip -q awscliv2.zip \
    && sudo ./aws/install \
    && rm -rf awscliv2.zip ./aws

# GitHub CLI, for PR and issue work from a Claude session. Debian ships no
# usable gh package and the upstream apt repo would be an unpinned external
# source, so this takes the release .deb at a pinned version instead. dpkg
# rather than apt-get because the package lists are deleted above; gh's only
# dependency is git, which the base image already has.
ARG GH_VERSION=2.97.0
RUN arch="$(dpkg --print-architecture)" \
    && curl -fsSL -o /tmp/gh.deb "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${arch}.deb" \
    && sudo dpkg -i /tmp/gh.deb \
    && rm /tmp/gh.deb

# jq, for the Makefile's IMAGE_URL: `make build` and `make push` read the ECR
# repo url out of config.json with it, and without jq make prints
# "jq: No such file or directory" on *every* invocation -- including targets
# that never look at IMAGE_URL, because that line is a `$(shell ...)` make
# expands up front. Interactive-only, like the awscli: the runtime stage gets
# built by `make push`, it doesn't run it.
RUN sudo apt-get update \
    && sudo apt-get install -y --no-install-recommends jq \
    && sudo rm -rf /var/lib/apt/lists/*

# Claude Code, pinned like poetry above. Installs the native build to
# ~/.local/share/claude/versions/<v> and symlinks a launcher into ~/.local/bin,
# which the PATH in `base` already covers. The installer's scratch dir
# (~/.claude/downloads) is emptied before the layer closes, so nothing here
# collides with the ~/.claude bind mount at runtime. Auto-updates still run
# inside a live container; this only pins what a fresh build starts from.
RUN curl -fsSL https://claude.ai/install.sh | bash -s 2.1.235

# Claude's top-level config lives at $HOME/.claude.json, which is *outside* the
# ~/.claude bind mount -- so a rebuild loses it and the first `claude` run
# replays the onboarding prompts. Seeding these two keys skips that; Claude
# fills in the rest of the file on first run.
RUN printf '%s' '{"hasCompletedOnboarding":true,"theme":"dark"}' \
    > /home/dev/.claude.json

# For persisting bash history
RUN SNIPPET="export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" && echo $SNIPPET >> "/home/dev/.bashrc"

# Machine-local environment -- the AWS_PROFILE to log into, mainly. It has to
# be settable without committing it, so it lives in .devcontainer/local.env,
# which .gitignore and .dockerignore both drop; devcontainer.json passes its
# path down as DEVCONTAINER_ENV_FILE (from ${containerWorkspaceFolder}, since
# where the workspace lands depends on which folder you opened).
#
# Sourced from two places on purpose, because bash reads different files
# depending on how it was started and both cases matter here:
#   .bashrc    interactive shells -- a VS Code terminal
#   BASH_ENV   non-interactive ones -- `bash -c`, which is how Claude Code and
#              anything scripted runs commands
# Between them every bash in the container ends up with the profile set,
# rather than just the ones a human typed into.
#
# Keep this script silent: BASH_ENV is read before *every* non-interactive
# bash, so anything it prints lands in the output of every `$(...)` capture.
COPY --chown=dev:dev .devcontainer/local-env.sh /home/dev/.local-env.sh
ENV BASH_ENV=/home/dev/.local-env.sh
RUN echo '. /home/dev/.local-env.sh' >> "/home/dev/.bashrc"

# Is this a good way to add stuff on load?
# Not sure, but it's working
COPY --chown=dev:dev profile.sh /home/dev/profile.sh
RUN echo ". /home/dev/profile.sh" >> "/home/dev/.bashrc"

RUN endgame -- --completion bash > ~/.endgame-completion.sh && echo ". ~/.endgame-completion.sh" >> "/home/dev/.bashrc"

# `endgame-aws` can't do the same at build time: it's the root package, which
# `poetry install --no-root` above skips deliberately (the source isn't in the
# image, it's the workspace mount). postStartCommand writes the script instead.
RUN echo 'if [ -f ~/.endgame-aws-completion.sh ]; then . ~/.endgame-aws-completion.sh; fi' >> "/home/dev/.bashrc"
