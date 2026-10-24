"""Isolate every test entry point before any application module is imported."""
import os
import tempfile
from pathlib import Path

_database_directory = tempfile.TemporaryDirectory(prefix="tongpin-tests-")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (Path(_database_directory.name) / "tests.db").as_posix()
os.environ["JWT_SECRET"] = "test-only-secret-that-is-long-enough-for-local-tests-123"
