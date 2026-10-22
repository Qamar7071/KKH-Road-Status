import os
import sys
import tempfile
from pathlib import Path

# Isolated SQLite file for every test run (must be set before kkh.config is imported)
os.environ["KKH_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
