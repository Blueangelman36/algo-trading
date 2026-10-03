"""
Bot launcher — reads a bot's JSON config and starts run_paper.py with it.

Why this exists: it lets the web dashboard change a bot's symbols/strategy by
editing a plain JSON file (which it owns, no root needed), instead of rewriting
systemd unit files (which would need root). systemd runs this launcher; the
launcher reads the config and becomes the actual bot process.

Config lives in bots/<name>.json, e.g. bots/uso-meanrev.json:
    {
      "symbols": ["USO"],
      "strategy": "meanrev",
      "interval": "5Min",
      "broker": "alpaca",
      "max_daily_loss": 3.0,
      "max_per_symbol": 0.25,
      "enabled": true
    }

Optional keys (all off/absent by default — existing configs run unchanged):
    "vol_target": 0.15        -> volatility-targeted sizing (annualized)
    "regime_filter": true     -> wrap strategy in the ER regime veto
    "news_filter" / "event_blackout" / "earnings_blackout" / "sec_blackout"

NOTE: the new strategies ("breakout", "pyramid", "xsec") and the new optional
keys require matching support in run_paper.py (--strategy choices and
--vol-target / --regime-filter flags). Until run_paper.py is updated, only
use them in a config if you've added those flags — run_bot.py validates the
JSON, but argparse in run_paper.py has the final say. Existing meanrev /
momentum / pairs configs are unaffected either way.

Usage:  python run_bot.py uso-meanrev
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BOTS_DIR = os.path.join(HERE, "bots")

VALID_STRATEGIES = {"meanrev", "momentum", "pairs",
                    "breakout", "pyramid", "xsec"}
VALID_INTERVALS = {"1Min", "5Min", "15Min", "1Hour", "1Day"}
VALID_BROKERS = {"alpaca", "ibkr"}
SYMBOL_RE = re.compile(r"^[A-Za-z0-9.\-=]{1,12}$")


def load_config(name: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", name):
        sys.exit(f"Invalid bot name: {name!r}")
    path = os.path.join(BOTS_DIR, f"{name}.json")
    if not os.path.exists(path):
        sys.exit(f"No config at {path}")
    with open(path) as f:
        cfg = json.load(f)

    # Validate everything before it becomes command-line arguments.
    symbols = cfg.get("symbols") or []
    if not symbols or not all(SYMBOL_RE.match(s) for s in symbols):
        sys.exit(f"Invalid symbols in {path}: {symbols!r}")
    if cfg.get("strategy") not in VALID_STRATEGIES:
        sys.exit(f"Invalid strategy in {path}: {cfg.get('strategy')!r}")
    if cfg.get("interval") not in VALID_INTERVALS:
        sys.exit(f"Invalid interval in {path}: {cfg.get('interval')!r}")
    if cfg.get("broker", "alpaca") not in VALID_BROKERS:
        sys.exit(f"Invalid broker in {path}: {cfg.get('broker')!r}")
    if cfg["strategy"] == "pairs" and len(symbols) != 2:
        sys.exit("Pairs strategy requires exactly 2 symbols.")
    if cfg["strategy"] == "xsec" and len(symbols) < 3:
        sys.exit("Cross-sectional momentum needs a universe (3+ symbols).")
    vt = cfg.get("vol_target")
    if vt is not None:
        try:
            vt = float(vt)
        except (TypeError, ValueError):
            sys.exit(f"vol_target must be a number, got {vt!r}")
        if not (0.01 <= vt <= 1.0):
            sys.exit(f"vol_target out of range (0.01-1.0): {vt}")
    return cfg


def build_argv(cfg: dict) -> list[str]:
    argv = [
        sys.executable, os.path.join(HERE, "run_paper.py"),
        "--broker", cfg.get("broker", "alpaca"),
        "--strategy", cfg["strategy"],
        "--interval", cfg["interval"],
        "--symbols", *cfg["symbols"],
        "--max-daily-loss", str(float(cfg.get("max_daily_loss", 3.0))),
        "--max-per-symbol", str(float(cfg.get("max_per_symbol", 0.25))),
    ]
    # Sizing / regime — only emitted when set, so old run_paper.py builds
    # never see flags they don't know.
    if cfg.get("vol_target") is not None:
        argv += ["--vol-target", str(float(cfg["vol_target"]))]
    if cfg.get("regime_filter"):
        argv.append("--regime-filter")
    # Catalyst filters — defensive vetoes around news/events/earnings/filings.
    if cfg.get("news_filter"):
        argv.append("--news-filter")
    if cfg.get("event_blackout"):
        argv.append("--event-blackout")
    if cfg.get("earnings_blackout"):
        argv.append("--earnings-blackout")
    if cfg.get("sec_blackout"):
        argv.append("--sec-blackout")
    return argv


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python run_bot.py <bot-name>")
    name = sys.argv[1]
    cfg = load_config(name)

    if not cfg.get("enabled", True):
        print(f"Bot '{name}' is disabled in its config; exiting.")
        sys.exit(0)

    argv = build_argv(cfg)
    print(f"Starting bot '{name}': {' '.join(argv[1:])}", flush=True)
    # Replace this process with the bot, so systemd tracks the real PID.
    os.execv(sys.executable, argv)


if __name__ == "__main__":
    main()
