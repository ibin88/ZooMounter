"""Environment loading that works no matter where you run from.

`load_dotenv()` with no arguments searches the current directory and its
parents. That's fine when you run from the repo, but the whole point of
putting ZooMounter on your PATH is to call it from wherever your CAD project
lives -- and from there, the repo's `.env` is nowhere up the tree, so the API
token silently doesn't load and the first API call fails with a confusing
"ZOO_API_TOKEN is not set".

So: check the working directory first (a project-local `.env` should still
win, which is what you'd want if you keep separate tokens per project), then
fall back to the one that ships next to the package.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# zoomounter/config.py -> zoomounter/ -> repo root
PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def load_environment() -> None:
    """Load .env from the working directory if there is one, then from the
    repo, without letting the second override the first."""
    load_dotenv()  # cwd and parents
    fallback = PACKAGE_ROOT / ".env"
    if fallback.exists():
        load_dotenv(fallback, override=False)


def zoo_cli_path() -> str:
    """Where to find the `zoo` binary. Defaults to bare `zoo`, which resolves
    via PATH -- set ZOO_CLI_PATH only if it isn't installed there."""
    return os.environ.get("ZOO_CLI_PATH", "zoo")
