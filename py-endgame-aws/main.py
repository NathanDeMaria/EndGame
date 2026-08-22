# The deployed image's ENTRYPOINT runs this file directly (the root package
# isn't pip-installed in the runtime target, so there's no `endgame-aws` on
# PATH there). Everything it dispatches to lives in endgame_aws.cli, which is
# also what the `endgame-aws` script points at in the devcontainer.
from endgame_aws.cli import main

if __name__ == "__main__":
    main()
