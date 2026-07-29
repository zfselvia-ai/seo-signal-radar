"""Deprecated: state handling moved to store.py. Kept as a thin shim."""
from .store import last_run, filter_unseen, commit_seen  # noqa: F401

# Backwards-compatible alias
commit = commit_seen
