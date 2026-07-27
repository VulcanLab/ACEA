"""
Test config for the evolution service.

`Settings` requires ANALYZER_MODEL / REWRITER_MODEL with no defaults, and the
module is imported at collection time, so both must be set before import.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("ANALYZER_MODEL", "test/analyzer")
os.environ.setdefault("REWRITER_MODEL", "test/rewriter")

# src/ holds both main.py and its sibling litellm_safe.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
