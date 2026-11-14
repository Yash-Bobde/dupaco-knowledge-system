"""The contract types (Answer, Conflict, Page, Snapshot, Source), taken from the harness.

The harness imports its `contract` module before it loads solution.py, so a plain import finds it
(and the harness then sees its own Answer class). Outside the harness, e.g. in our own scripts,
we walk up the parent folders to find harness/contract.py, the same way the kit's starter does.
"""
import sys
from pathlib import Path

try:
    from contract import Answer, Conflict, Page, Snapshot, Source
except ImportError:
    for _parent in Path(__file__).resolve().parents:
        if (_parent / "harness" / "contract.py").exists():
            sys.path.insert(0, str(_parent / "harness"))
            break
    from contract import Answer, Conflict, Page, Snapshot, Source

__all__ = ["Answer", "Conflict", "Page", "Snapshot", "Source"]
