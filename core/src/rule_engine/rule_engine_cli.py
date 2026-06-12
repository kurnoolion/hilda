"""User-facing CLI wrapper for ops debugging per [D-005].

Invoked as: python -m core.src.rule_engine.rule_engine_cli --diagnostic | --validate |
--explain --trigger <kind> --entity '<json>' [--sub-trigger <s>] [--field-deltas '<json>']
[--derived-fields '<json>']

Implementation lives in diagnostics_cli.main.
"""
from __future__ import annotations

import sys

from .diagnostics_cli import main

__all__ = ["main"]

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
