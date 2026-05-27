from typing import Optional
from pathlib import Path

from dotenv import load_dotenv


def _nearest_dotenv(start: Path) -> Optional[Path]:
    """Return the nearest ``.env`` at ``start`` or any ancestor, else None.

    Walks upward like ``dotenv.find_dotenv`` so a checkout that has no ``.env`` of
    its own inherits the nearest ancestor's. This is what lets a **git worktree**
    (``<main>/.claude/worktrees/<name>/``) pick up the main repo's gitignored
    ``.env`` at ``<main>/.env`` — otherwise the worktree, which never contains the
    untracked ``.env``, loads no Azure credentials at all.
    """
    try:
        start = start.resolve()
    except OSError:
        return None
    for d in (start, *start.parents):
        candidate = d / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_shinka_dotenv(
    package_root: Optional[Path] = None, cwd: Optional[Path] = None
) -> tuple[Path, ...]:
    """Load package and launch-directory dotenv files, with launch-dir precedence.

    Both locations are resolved by walking *upward* to the nearest ``.env`` (see
    ``_nearest_dotenv``), so the package can be imported from a nested directory
    (e.g. a git worktree) and still find the repo-root credentials file.
    """
    resolved_package_root = (
        package_root if package_root is not None else Path(__file__).resolve().parents[1]
    )
    resolved_cwd = cwd if cwd is not None else Path.cwd()

    env_paths: list[Path] = []
    package_env = _nearest_dotenv(resolved_package_root)
    launch_env = _nearest_dotenv(resolved_cwd)

    # Package-side first, launch-side last so launch-dir precedence holds
    # (load_dotenv override=True lets the later file win). Dedup when both
    # resolve to the same file (the common in-repo case).
    if package_env is not None:
        env_paths.append(package_env)
    if launch_env is not None and launch_env not in env_paths:
        env_paths.append(launch_env)

    for env_path in env_paths:
        load_dotenv(dotenv_path=env_path, override=True)

    return tuple(env_paths)
