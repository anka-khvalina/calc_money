"""Orchestrate market-weights offline experiments."""

from __future__ import annotations

import sys


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"041", "044", "weights", "run"}:
        from .run import main as run_main

        return run_main()
    if argv[0] in {"045", "exp045", "revaluation"}:
        from .exp045 import main as exp045_main

        return exp045_main()
    print("Usage: python -m experiments.market_weights [run|045]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
