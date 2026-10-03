"""Run the demo headless on synthetic data and press every tab's Run button.

    pip install -r demo/requirements.txt
    python demo/smoke_test.py

Fails on any exception or error message the app shows, so CI catches a
change in backtest/, research/ or strategies/ that breaks the demo.
"""
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent / "streamlit_app.py")
RUNS = ["bt_run", "scr_run", "wf_run", "opt_run"]


def main() -> int:
    at = AppTest.from_file(APP, default_timeout=300).run()
    failures = [f"on load: {e.value}" for e in at.exception]
    for key in RUNS:
        at.button(key=key).click().run()
        failures += [f"{key}: {e.value}" for e in at.exception]
        failures += [f"{key}: {e.value}" for e in at.error]
    for strategy in ("pairs", "xsec", "pyramid", "breakout"):
        at.selectbox(key="bt_strategy").set_value(strategy).run()
        at.button(key="bt_run").click().run()
        failures += [f"bt_run ({strategy}): {e.value}" for e in at.exception]
        failures += [f"bt_run ({strategy}): {e.value}" for e in at.error]
    print(f"{len(at.metric)} stat tiles rendered across the four tabs")
    if failures:
        print("FAILED:\n  " + "\n  ".join(failures))
        return 1
    print("demo smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
