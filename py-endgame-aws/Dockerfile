# What runs on Batch: `python main.py <command>`, and nothing else.
#
# 3.15.2-trixie, pinned by the digest of the multi-platform index -- the same
# image mirrorball's devcontainer uses, so the job and the devcontainer run
# one interpreter. The full image rather than -slim because it already has git,
# which uv needs to clone `endgame` at its pinned rev.
FROM python@sha256:a51b0cd81c9c0fc08afc4a823843b459dc71c9386a02ccb150097b41d783f33b

# uv at the version mirrorball pins, copied out of its own image (by index
# digest, ghcr.io/astral-sh/uv:0.13.6) rather than pip-installed into the
# interpreter the job runs on.
COPY --from=ghcr.io/astral-sh/uv@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 /uv /uvx /usr/local/bin/

# Non-root, uid 1000, as it has been: ~/.endgame/cache and
# ~/.aws-batch/config.json resolve to the same paths in a Batch job as on a
# laptop. ~/.endgame is where a job writes its game cache, so it's created
# here, owned by the user.
RUN useradd --uid 1000 --create-home dev
USER dev
RUN mkdir -p /home/dev/.endgame
WORKDIR /home/dev/runtime

# The interpreter above, never one uv downloads; bytecode compiled at build
# time so a job doesn't pay for it on every start.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/home/dev/runtime/.venv/bin:${PATH}

# Dependencies before the code, so editing endgame_aws/ doesn't reinstall them.
COPY --chown=dev:dev pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

# The package itself is never installed: main.py sits beside endgame_aws/ and
# imports it from there, and config.json at this level is where Config finds
# the bucket (its first candidate, the package root). Required at build time,
# so a missing bucket fails the build rather than every job; CI writes it from
# the shared stack's outputs.
COPY --chown=dev:dev main.py config.json ./
COPY --chown=dev:dev endgame_aws ./endgame_aws

ENTRYPOINT ["python", "/home/dev/runtime/main.py"]
CMD ["--", "--help"]
