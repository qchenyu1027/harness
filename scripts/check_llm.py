#!/usr/bin/env python3
"""Source-checkout entry point for the robot LLM connectivity check."""

import sys
from pathlib import Path


# Allow the documented command to work from a source checkout before installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness2.cli.check_llm import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
