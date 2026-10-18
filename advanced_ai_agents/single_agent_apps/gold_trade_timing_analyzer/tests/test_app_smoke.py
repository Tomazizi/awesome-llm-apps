"""
Streamlit AppTest smoke test: runs the whole app head-less and asserts it does
not raise. Bridge waiting is capped so the run stays fast when no browser is
attached (which is the case in CI).

    python tests/test_app_smoke.py
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = os.path.join(HERE, "gold_trade_timing_analyzer.py")


def main() -> int:
    t0 = time.time()
    at = AppTest.from_file(APP, default_timeout=900)
    at.session_state["bridge_cap"] = 6.0
    at.run()
    dur = time.time() - t0

    print(f"ran in {dur:.1f}s")
    if at.exception:
        print("\n=== EXCEPTION ===")
        for e in at.exception:
            print(getattr(e, "value", e))
        return 1

    print(f"markdown blocks: {len(at.markdown)}")
    print(f"dataframes:      {len(at.dataframe)}")
    print(f"plotly charts:   {len(at.get('plotly_chart'))}")
    print(f"tabs:            {len(at.tabs)}")
    print("\n--- first 12 markdown blocks ---")
    for m in at.markdown[:12]:
        print("  •", " ".join(str(m.value).split())[:150])
    return 0


if __name__ == "__main__":
    sys.exit(main())
