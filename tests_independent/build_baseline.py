"""Regenerate ``baseline_metrics.json`` from the raw CSV registry.

Run only when the registry itself is intentionally replaced:

    .venv/bin/python -m tests_independent.build_baseline

The normal test suite treats the JSON file as frozen ground truth.
"""

from __future__ import annotations

import json
import os

from tests_independent.reference import compute_metrics

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "baseline_metrics.json")


def main() -> None:
    metrics = compute_metrics()
    payload = {
        "warning": (
            "Frozen reference metrics computed from uzex_spot_data/*.csv "
            "according to README.md. Regenerate only if the registry changes."
        ),
        "metrics": metrics,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
