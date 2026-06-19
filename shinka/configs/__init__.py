"""Packaged Hydra configs retained for the upstream load path (``shinka.utils.utils_hydra``).

(The Shinka CLI was removed in the Azure-only prune.)
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path


@contextmanager
def config_root() -> Iterator[Path]:
    """Yield a filesystem path to packaged Hydra configs."""
    with as_file(files(__name__)) as path:
        yield Path(path)
