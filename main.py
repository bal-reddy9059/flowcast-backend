"""Compatibility entrypoint for hosts configured with ``uvicorn main:app``.

The maintained application lives in :mod:`app.main`. Keeping this shim avoids
starting the obsolete root application when a deployment platform retains an
older start command.
"""

from app.main import app

__all__ = ["app"]
