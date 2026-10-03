"""
Web dashboard for the trading framework.

WHAT IT DOES
  - Research tab: run the screener / backtest / walk-forward from a browser
    form. Jobs run in the BACKGROUND and the page polls for results, so a
    15-minute walk-forward never freezes the dashboard (or your Stop buttons).
  - Bots tab: see each bot's live status, edit its symbols/strategy/interval/
    sizing, and start/stop/restart it.
  - Logs tab: tail a bot's output, with auto-refresh.

SECURITY MODEL (read this — it's why the code looks the way it does)
  1. Every input is validated against a whitelist BEFORE it can become a
     command-line argument. Symbols must match a strict regex; strategy,
     interval, and broker must be members of fixed sets; numbers are cast
     and range-checked.
  2. Subprocesses are launched with an ARGUMENT LIST and never shell=True.
  3. The app never edits system files. Bot settings are JSON files it owns.
     The only privileged thing it does is `systemctl` on units matching
     trading-bot@<name>, permitted by a narrow sudoers rule.
  4. Password-gated (no default), with rate-limited login attempts.
  5. Every state-changing POST requires a per-session CSRF token.
  6. Login `next` redirects are restricted to local paths (no open redirect).
  7. Intended to sit behind HTTPS or an SSH tunnel. Never plain HTTP over
     the internet.

CHANGES vs previous version
  - Research jobs are asynchronous: POST starts a background thread, the
    browser polls /research/status/<id>. Job state lives in files under
    PROJECT_DIR/.dashboard-jobs so it works across gunicorn workers.
  - CSRF tokens on all POSTs; SameSite=Lax set explicitly.
  - Secret key: uses DASHBOARD_SECRET_KEY, else derives a STABLE key from
    the password hash — the old per-process os.urandom fallback logged you
    out at random with more than one gunicorn worker.
  - Login: 0.5s delay + lockout after repeated failures; `next` sanitized.
  - Start/Stop now use `systemctl enable --now` / `disable --now`, so a
    stopped bot STAYS stopped across reboots and a started one comes back.
    Delete disables the unit first — previously a deleted bot's enabled unit
    went into a restart loop after reboot. REQUIRES the updated sudoers file.
  - New strategies (breakout, pyramid, xsec) + vol_target + regime_filter
    exposed in the bot form and passed through to run_bot.py configs.
  - /api/bots JSON endpoint for live status polling; /logs raw mode for
    auto-refresh.

This dashboard controls PAPER trading. Not financial advice.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import threading
import time
import uuid
from functools import wraps

from flask import (Flask, Response, abort, flash, jsonify, redirect,
                   render_template, request, session, url_for)

# ---- configuration ----------------------------------------------------
PROJECT_DIR = os.environ.get(
    "PROJECT_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYTHON_BIN = os.environ.get(
    "PYTHON_BIN", os.path.join(PROJECT_DIR, "venv", "bin", "python"))
BOTS_DIR = os.path.join(PROJECT_DIR, "bots")
JOBS_DIR = os.path.join(PROJECT_DIR, ".dashboard-jobs")
PASSWORD = os.environ.get("DASHBOARD_PASSWORD")
UNIT_PREFIX = "trading-bot@"

app = Flask(__name__)

# Secret key: explicit env var wins. Otherwise derive a key from the
# password so it is IDENTICAL across gunicorn workers — the old fallback
# (os.urandom per process) made each worker sign sessions differently,
# which bounced you to the login page at random with --workers 2.
_secret = os.environ.get("DASHBOARD_SECRET_KEY")
if not _secret and PASSWORD:
    _secret = hashlib.sha256(b"dashboard-secret-v1:" +
                             PASSWORD.encode()).hexdigest()
app.secret_key = _secret or os.urandom(32).hex()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

# ---- validation whitelists --------------------------------------------
STRATEGIES = ["meanrev", "momentum", "pairs", "breakout", "pyramid", "xsec"]
STRATEGY_LABELS = {
    "meanrev": "Mean reversion",
    "momentum": "Momentum (time-series)",
    "pairs": "Pairs / stat-arb",
    "breakout": "Breakout (Donchian)",
    "pyramid": "Pyramiding breakout",
    "xsec": "Cross-sectional momentum",
}
INTERVALS = ["1Min", "5Min", "15Min", "1Hour", "1Day"]
BROKERS = ["alpaca", "ibkr"]
RESEARCH_TOOLS = ["screener", "backtest", "walkforward"]
SYMBOL_RE = re.compile(r"^[A-Za-z0-9.\-=]{1,12}$")
BOTNAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
JOBID_RE = re.compile(r"^[a-f0-9]{12}$")

# UI action -> systemctl argv. Start/stop use enable/disable --now so the
# on/off state survives reboots (and delete can't leave a zombie unit).
ACTION_ARGV = {
    "start": ["enable", "--now"],
    "stop": ["disable", "--now"],
    "restart": ["restart"],
}


def clean_symbols(raw: str) -> list[str]:
    """Parse a whitespace/comma separated symbol string, rejecting anything
    that isn't a plausible ticker. This is the main injection guard."""
    parts = [p.strip().upper() for p in re.split(r"[\s,]+", raw or "") if p.strip()]
    if not parts:
        raise ValueError("No symbols given.")
    if len(parts) > 12:
        raise ValueError("Too many symbols (max 12).")
    for p in parts:
        if not SYMBOL_RE.match(p):
            raise ValueError(f"Invalid symbol: {p!r}")
    return parts


def clean_choice(value, allowed, label):
    if value not in allowed:
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def clean_number(value, label, lo, hi, default):
    if value is None or str(value).strip() == "":
        return default
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number.")
    if not (lo <= n <= hi):
        raise ValueError(f"{label} must be between {lo} and {hi}.")
    return n


def validate_strategy_symbols(strategy: str, symbols: list[str]):
    if strategy == "pairs" and len(symbols) != 2:
        raise ValueError("Pairs trading needs exactly 2 symbols.")
    if strategy == "xsec" and len(symbols) < 3:
        raise ValueError("Cross-sectional momentum needs a universe "
                         "(3+ symbols) to rank.")


# ---- auth + CSRF --------------------------------------------------------
def get_csrf() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = get_csrf


@app.before_request
def csrf_protect():
    # Every POST except the login form itself must carry the session token.
    if request.method == "POST" and request.endpoint not in ("login",):
        tok = session.get("_csrf")
        given = request.form.get("_csrf", "")
        if not tok or not hmac.compare_digest(given, tok):
            abort(400, "Missing or invalid CSRF token — reload the page.")


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("auth"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def _safe_next(raw: str | None) -> str:
    """Only allow same-site relative paths — never absolute URLs. Fixes the
    open redirect (?next=https://evil.example)."""
    if raw and raw.startswith("/") and not raw.startswith("//") \
            and ":" not in raw.split("?")[0]:
        return raw
    return url_for("bots")


# Simple in-memory login throttle. Per-process (fine for one gunicorn
# worker; with several, the effective allowance multiplies but stays small).
_FAILS: dict = {}
_MAX_FAILS, _LOCK_SECS = 8, 300


def _throttled(ip: str) -> bool:
    n, first = _FAILS.get(ip, (0, 0.0))
    if n >= _MAX_FAILS and time.time() - first < _LOCK_SECS:
        return True
    if time.time() - first >= _LOCK_SECS:
        _FAILS.pop(ip, None)
    return False


def _record_fail(ip: str):
    n, first = _FAILS.get(ip, (0, time.time()))
    _FAILS[ip] = (n + 1, first if n else time.time())


@app.route("/login", methods=["GET", "POST"])
def login():
    if not PASSWORD:
        return ("DASHBOARD_PASSWORD is not set on the server. "
                "Refusing to run without a password."), 500
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if _throttled(ip):
            flash("Too many failed attempts — locked out for 5 minutes.", "error")
            return render_template("login.html"), 429
        given = request.form.get("password", "")
        if hmac.compare_digest(given, PASSWORD):
            _FAILS.pop(ip, None)
            session["auth"] = True
            get_csrf()
            return redirect(_safe_next(request.args.get("next")))
        _record_fail(ip)
        time.sleep(0.5)   # slow down guessing
        flash("Incorrect password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---- subprocess helpers -------------------------------------------------
def run_cmd(argv: list[str], timeout=600) -> tuple[bool, str]:
    """Run a command as an argument list (never a shell string)."""
    try:
        proc = subprocess.run(argv, cwd=PROJECT_DIR, capture_output=True,
                              text=True, timeout=timeout)
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, out.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return False, f"Timed out after {timeout}s."
    except FileNotFoundError as e:
        return False, f"Command not found: {e}"


def unit_status(name: str) -> str:
    ok, out = run_cmd(["systemctl", "is-active", f"{UNIT_PREFIX}{name}.service"],
                      timeout=10)
    return (out or "unknown").splitlines()[0].strip()


def systemctl(action: str, name: str) -> tuple[bool, str]:
    if action not in ACTION_ARGV or not BOTNAME_RE.match(name):
        return False, "Rejected: invalid action or bot name."
    argv = ["sudo", "-n", "systemctl", *ACTION_ARGV[action],
            f"{UNIT_PREFIX}{name}.service"]
    return run_cmd(argv, timeout=30)


def list_bots(with_status=True) -> list[dict]:
    os.makedirs(BOTS_DIR, exist_ok=True)
    bots = []
    for fn in sorted(os.listdir(BOTS_DIR)):
        if not fn.endswith(".json"):
            continue
        name = fn[:-5]
        if not BOTNAME_RE.match(name):
            continue
        try:
            with open(os.path.join(BOTS_DIR, fn)) as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        cfg["name"] = name
        if with_status:
            cfg["status"] = unit_status(name)
        bots.append(cfg)
    return bots


def save_bot(name: str, cfg: dict):
    if not BOTNAME_RE.match(name):
        raise ValueError("Invalid bot name.")
    os.makedirs(BOTS_DIR, exist_ok=True)
    path = os.path.join(BOTS_DIR, f"{name}.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)


# ---- background research jobs -------------------------------------------
# Job state is FILES under PROJECT_DIR/.dashboard-jobs, not process memory,
# so polling works no matter which gunicorn worker answers.

def _job_path(job_id: str) -> str:
    return os.path.join(JOBS_DIR, f"{job_id}.json")


def _write_job(job_id: str, data: dict):
    os.makedirs(JOBS_DIR, exist_ok=True)
    tmp = _job_path(job_id) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, _job_path(job_id))


def read_job(job_id: str) -> dict | None:
    if not JOBID_RE.match(job_id or ""):
        return None
    try:
        with open(_job_path(job_id)) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _prune_jobs(max_age=86400):
    try:
        now = time.time()
        for fn in os.listdir(JOBS_DIR):
            p = os.path.join(JOBS_DIR, fn)
            if now - os.path.getmtime(p) > max_age:
                os.remove(p)
    except OSError:
        pass


def _run_job(job_id: str, argv: list[str], cmd_shown: str, started: float):
    ok, out = run_cmd(argv, timeout=1800)
    _write_job(job_id, {"status": "done" if ok else "error", "cmd": cmd_shown,
                        "output": out, "started": started,
                        "elapsed": round(time.time() - started, 1)})


def start_job(argv: list[str], cmd_shown: str) -> str:
    os.makedirs(JOBS_DIR, exist_ok=True)
    _prune_jobs()
    job_id = uuid.uuid4().hex[:12]
    started = time.time()
    _write_job(job_id, {"status": "running", "cmd": cmd_shown,
                        "output": "", "started": started})
    threading.Thread(target=_run_job, args=(job_id, argv, cmd_shown, started),
                     daemon=True).start()
    return job_id


# ---- routes: bots -------------------------------------------------------
@app.route("/")
@login_required
def index():
    return redirect(url_for("bots"))


@app.route("/bots")
@login_required
def bots():
    return render_template("bots.html", bots=list_bots(),
                           strategies=STRATEGIES, labels=STRATEGY_LABELS,
                           intervals=INTERVALS, brokers=BROKERS)


@app.route("/api/bots")
@login_required
def api_bots():
    return jsonify([{"name": b["name"], "status": b.get("status", "unknown")}
                    for b in list_bots(with_status=True)])


@app.route("/bots/save", methods=["POST"])
@login_required
def bots_save():
    try:
        name = request.form.get("name", "").strip()
        if not BOTNAME_RE.match(name):
            raise ValueError("Bot name must be letters, numbers, - or _.")
        symbols = clean_symbols(request.form.get("symbols", ""))
        strategy = clean_choice(request.form.get("strategy"), STRATEGIES, "strategy")
        interval = clean_choice(request.form.get("interval"), INTERVALS, "interval")
        broker = clean_choice(request.form.get("broker", "alpaca"), BROKERS, "broker")
        validate_strategy_symbols(strategy, symbols)

        # Guard: don't let two bots fight over the same symbol.
        for other in list_bots(with_status=False):
            if other["name"] == name:
                continue
            overlap = set(symbols) & set(other.get("symbols", []))
            if overlap:
                raise ValueError(
                    f"Symbol(s) {', '.join(sorted(overlap))} already traded by "
                    f"bot '{other['name']}'. Two bots on one symbol will fight "
                    f"over the position.")

        cfg = {
            "symbols": symbols,
            "strategy": strategy,
            "interval": interval,
            "broker": broker,
            "max_daily_loss": clean_number(request.form.get("max_daily_loss"),
                                           "Max daily loss", 0.5, 50, 3.0),
            "max_per_symbol": clean_number(request.form.get("max_per_symbol"),
                                           "Max per symbol", 0.01, 1.0, 0.25),
            # Catalyst filters: defensive vetoes. Checkboxes -> booleans.
            "news_filter": bool(request.form.get("news_filter")),
            "event_blackout": bool(request.form.get("event_blackout")),
            "earnings_blackout": bool(request.form.get("earnings_blackout")),
            "sec_blackout": bool(request.form.get("sec_blackout")),
            "regime_filter": bool(request.form.get("regime_filter")),
            "enabled": True,
        }
        # Vol targeting is optional: blank = off (key omitted entirely, so
        # run_bot.py emits no flag and old run_paper.py builds still work).
        vt = clean_number(request.form.get("vol_target"),
                          "Vol target", 0.01, 1.0, None)
        if vt is not None:
            cfg["vol_target"] = vt

        save_bot(name, cfg)
        flash(f"Saved '{name}'. Restart it to apply the changes.", "ok")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("bots"))


@app.route("/bots/<name>/<action>", methods=["POST"])
@login_required
def bots_action(name, action):
    ok, out = systemctl(action, name)
    verb = {"start": "started (and enabled at boot)",
            "stop": "stopped (and disabled at boot)",
            "restart": "restarted"}.get(action, action)
    flash(f"{name}: {verb if ok else action + ' FAILED'} — {out[:300]}",
          "ok" if ok else "error")
    return redirect(url_for("bots"))


@app.route("/bots/<name>/delete", methods=["POST"])
@login_required
def bots_delete(name):
    if not BOTNAME_RE.match(name):
        flash("Invalid bot name.", "error")
        return redirect(url_for("bots"))
    # disable --now: stops it AND removes the boot symlink. Previously a
    # deleted bot's still-enabled unit restart-looped forever after reboot.
    systemctl("stop", name)
    path = os.path.join(BOTS_DIR, f"{name}.json")
    if os.path.exists(path):
        os.remove(path)
        flash(f"Deleted bot '{name}' (stopped and disabled first).", "ok")
    return redirect(url_for("bots"))


# ---- routes: logs -------------------------------------------------------
@app.route("/logs")
@app.route("/logs/<name>")
@login_required
def logs(name=None):
    output = ""
    if name:
        if not BOTNAME_RE.match(name):
            output = "Invalid bot name."
        else:
            # NOTE: keep these arguments EXACTLY in sync with the pinned
            # journalctl line in deploy/sudoers-trading.
            ok, output = run_cmd(
                ["sudo", "-n", "journalctl", "-u", f"{UNIT_PREFIX}{name}.service",
                 "-n", "200",  # asof:journal-lines
                 "--no-pager"], timeout=30)
    if request.args.get("raw") == "1":
        return Response(output, mimetype="text/plain")
    return render_template("logs.html", bots=list_bots(with_status=False),
                           selected=name, output=output)


# ---- routes: research ----------------------------------------------------
@app.route("/research", methods=["GET", "POST"])
@login_required
def research():
    form = {"tool": "screener", "symbols": "USO BNO XLE XOM CVX COP",
            "strategy": "meanrev", "start": "2022-01-01",
            "train": "252", "test": "63"}

    if request.method == "POST":
        try:
            tool = clean_choice(request.form.get("tool"), RESEARCH_TOOLS, "tool")
            symbols = clean_symbols(request.form.get("symbols", ""))
            strategy = clean_choice(request.form.get("strategy"), STRATEGIES,
                                    "strategy")
            start = request.form.get("start", "2022-01-01").strip()
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
                raise ValueError("Start date must look like 2022-01-01.")

            if tool == "screener":
                if len(symbols) < 2:
                    raise ValueError("The screener needs at least 2 symbols.")
                argv = [PYTHON_BIN, "run_screener.py", "--symbols", *symbols,
                        "--start", start]
            elif tool == "backtest":
                validate_strategy_symbols(strategy, symbols)
                argv = [PYTHON_BIN, "run_backtest.py", "--symbols", *symbols,
                        "--strategy", strategy, "--start", start, "--trades"]
            else:  # walkforward
                validate_strategy_symbols(strategy, symbols)
                train = int(clean_number(request.form.get("train"),
                                         "Train", 30, 2000, 252))
                test = int(clean_number(request.form.get("test"),
                                        "Test", 10, 500, 63))
                argv = [PYTHON_BIN, "run_walkforward.py", "--symbols", *symbols,
                        "--strategy", strategy, "--start", start,
                        "--train", str(train), "--test", str(test)]

            job_id = start_job(argv, " ".join(argv[1:]))
            # Redirect (PRG) so refresh re-checks the job instead of
            # re-running it.
            return redirect(url_for("research", job=job_id))
        except ValueError as e:
            flash(str(e), "error")

    job_id = request.args.get("job", "")
    job = read_job(job_id) if job_id else None
    if job:
        form_keys = ("tool", "symbols", "strategy", "start", "train", "test")
        # Best-effort: reflect the job's command back into the form fields.
        # (Cosmetic only; not parsed for execution.)
    return render_template("research.html", strategies=STRATEGIES,
                           labels=STRATEGY_LABELS, form=form,
                           job=job, job_id=job_id if job else None)


@app.route("/research/status/<job_id>")
@login_required
def research_status(job_id):
    job = read_job(job_id)
    if job is None:
        return jsonify({"status": "missing"}), 404
    if job.get("status") == "running":
        job = dict(job)
        job["elapsed"] = round(time.time() - job.get("started", time.time()), 1)
        job.pop("output", None)   # don't ship partial/empty output every poll
    return jsonify(job)


@app.route("/api/events")
@login_required
def api_events():
    """Upcoming scheduled catalysts (next 7 days) from the event calendar —
    the same source the bots' event_blackout filter uses, so what you see
    here is exactly what the bots will stand aside for. Degrades gracefully
    if research/ isn't installed."""
    try:
        import sys
        if PROJECT_DIR not in sys.path:
            sys.path.insert(0, PROJECT_DIR)
        import pandas as pd
        from research.event_calendar import events_between, stale_after
        now = pd.Timestamp.utcnow()
        # Look back far enough to keep a weekend OPEC meeting listed until its
        # blackout ends at the next session, as the bots still observe it.
        recent = now - pd.Timedelta(hours=6)
        evs = [e for e in events_between(now - pd.Timedelta(days=5),
                                         now + pd.Timedelta(days=7))
               if e.when >= recent or (e.next_open is not None
                                       and e.next_open >= recent)]
        stale = stale_after()
        return jsonify({
            "events": [{"name": e.name,
                        "when": e.when.isoformat(),
                        "severity": e.severity,
                        "symbols": sorted(e.symbols)} for e in evs],
            "opec_stale_after": stale.isoformat(),
            "opec_stale": bool(now > stale),
        })
    except Exception as e:
        return jsonify({"error": f"event calendar unavailable: {e}"}), 200


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


if __name__ == "__main__":
    # Bind to localhost by default. Put a reverse proxy (with HTTPS) in front,
    # or reach it through an SSH tunnel. See WEB_DASHBOARD.md.
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "8000"))  # asof:dashboard-port
    app.run(host=host, port=port)
