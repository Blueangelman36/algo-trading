"""Print a number the docs state, read from the file that actually decides it.

asof (see asof.ini) runs these and compares the answer with every place the
docs repeat the number, so a doc can't keep saying "every 8 seconds" after the
template changed to 10. Each claim reads source text only: nothing is
imported, so it runs in CI without the trading dependencies installed.

    python tools/claims.py status-refresh-seconds
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _find(path: str, pattern: str) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    m = re.search(pattern, text)
    if not m:
        sys.exit(f"claims: {pattern!r} not found in {path}; "
                 f"update tools/claims.py to match the code.")
    return m.group(1)


def status_refresh_seconds() -> str:
    ms = _find("webapp/templates/bots.html", r"setInterval\(refreshStatus,\s*(\d+)\)")
    return str(int(ms) // 1000)


def log_refresh_seconds() -> str:
    # The log poller is an inline async arrow; its interval closes the call.
    ms = _find("webapp/templates/logs.html", r"\}\s*,\s*(\d+)\s*\);\s*</script>")
    return str(int(ms) // 1000)


def research_job_cap_minutes() -> str:
    s = _find("webapp/app.py", r"def _run_job\(.*\n\s*ok, out = run_cmd\(argv, timeout=(\d+)\)")
    return str(int(s) // 60)


def journal_lines() -> str:
    # The pinned sudo rule is the side the app has to match, so it is the
    # source of truth; webapp/app.py carries the marker.
    return _find("deploy/sudoers-trading", r"journalctl -u \S+ -n (\d+) --no-pager")


def dashboard_port() -> str:
    # What gunicorn binds on the server, which is where the docs send you.
    return _find("deploy/dashboard.service", r"--bind [\d.]+:(\d+)")


CLAIMS = {
    "status-refresh-seconds": status_refresh_seconds,
    "log-refresh-seconds": log_refresh_seconds,
    "research-job-cap-minutes": research_job_cap_minutes,
    "journal-lines": journal_lines,
    "dashboard-port": dashboard_port,
}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in CLAIMS:
        sys.exit(f"usage: python tools/claims.py {{{','.join(CLAIMS)}}}")
    print(CLAIMS[sys.argv[1]]())
