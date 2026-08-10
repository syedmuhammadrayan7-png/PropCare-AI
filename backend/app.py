import sys
from pathlib import Path

from main import app

repository_root = Path(__file__).resolve().parent.parent
if str(repository_root) not in sys.path:
    sys.path.insert(0, str(repository_root))

from backend.main import app

__all__ = ["app"]
