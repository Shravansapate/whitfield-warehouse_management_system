import sys
from pathlib import Path

# Ensure parent root folder is in sys.path if executed from inside backend directory
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.apis.api import app

__all__ = ["app"]


def run() -> None:
    """Run the development Uvicorn server.

    Uses import-string loading so reload mode can restart safely.
    """
    import uvicorn

    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    run()
