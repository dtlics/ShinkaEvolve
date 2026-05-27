"""run_detached.py — launch a window run that survives idle-sleep & the agent shell.

WHY THIS EXISTS (root-caused 2026-05-27): long orchestrator runs were getting
killed mid-run. The cause was NOT a bug in run_window — it ran fine for as long as
the machine stayed awake. The kill came from the *host*: on battery, macOS
idle-slept during long gaps (`pmset -g log` showed "Entering Sleep state due to
'Sleep Service Back to Sleep' ... Using Batt"), and the run_window process —
launched as a child of the agent's background-bash shell — was reaped across that
sleep/idle (no clean exit line was ever written, i.e. an external signal, not an
internal crash). The Claude app's own `NoIdleSleepAssertion` only blocks
display/idle sleep, not system idle-sleep on battery.

This launcher removes BOTH coupling points:
  1. ``caffeinate -ims`` wraps the run so the host will not IDLE-sleep for the
     run's lifetime. ``-i`` asserts PreventUserIdleSystemSleep, which holds on
     battery (``-s`` only applies on AC; harmless otherwise). The assertion is
     released automatically when run_window exits, because caffeinate runs it as
     its child. LIMIT: a closed lid on battery forces clamshell sleep that
     caffeinate cannot override — keep the lid open (or stay on AC) for long
     unattended runs.
  2. ``start_new_session=True`` puts the run in its OWN session/process-group with
     stdin detached and output to a log file, so it is NOT a descendant the agent
     harness can reap when the launching turn ends. The launcher returns
     immediately; the run continues independently.

The orchestrator then MONITORS via the journal (``journal/run.json`` +
``windows.jsonl``) and the PID file — not via a single long-lived bash job — and
RECOVERS from any kill with ``run_window.py --resume`` (the archive is written per
candidate and ``--resume`` reads window_state from the journal, so no work is lost
beyond the in-flight candidate).

MUTABILITY: harness plumbing. Not a strategy file.

USAGE:
  python orchestrator/harness/run_detached.py --config <run>/run.json [-- <run_window args>]
  # e.g.  ... --config r.json --until-decision --resume
  # prints {detached_pid, log, pid_file, ...}; poll the journal for progress.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_HARNESS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _HARNESS_DIR.parent.parent


def main() -> None:
    ap = argparse.ArgumentParser(description="Launch run_window detached + caffeinated.")
    ap.add_argument("--config", required=True, help="path to run config JSON")
    ap.add_argument(
        "--python", default=sys.executable,
        help="interpreter for run_window (default: this one). The eval subprocess "
             "inherits it via sys.executable, so pass the shinka env python.",
    )
    ap.add_argument(
        "--no-caffeinate", action="store_true",
        help="skip the caffeinate wrapper (e.g. on non-macOS or under CI)",
    )
    args, passthrough = ap.parse_known_args()

    cfg = json.loads(Path(args.config).read_text())
    results_dir = cfg["results_dir"]
    os.makedirs(results_dir, exist_ok=True)
    log_path = os.path.join(results_dir, "run_window.out")
    pid_path = os.path.join(results_dir, "run.pid")

    inner = [args.python, str(_HARNESS_DIR / "run_window.py"), "--config", args.config, *passthrough]
    use_caffeinate = (not args.no_caffeinate) and sys.platform == "darwin" and os.path.exists("/usr/bin/caffeinate")
    cmd = (["/usr/bin/caffeinate", "-ims", *inner]) if use_caffeinate else inner

    logf = open(log_path, "ab")
    logf.write(f"\n===== run_detached launch: {' '.join(cmd)} =====\n".encode())
    logf.flush()
    child_env = dict(os.environ)
    if use_caffeinate:
        # We already hold the no-idle-sleep assertion via the outer caffeinate;
        # tell the inner run_window to skip its own self-caffeinate (no double).
        child_env["SHINKA_CAFFEINATED"] = "1"
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=logf,
        stderr=logf,
        start_new_session=True,  # detach: own session, survives the agent shell
        cwd=str(_REPO_ROOT),     # consistent shinka resolution + .env discovery
        env=child_env,
    )
    Path(pid_path).write_text(str(proc.pid))
    print(json.dumps(
        {
            "ok": True,
            "detached_pid": proc.pid,
            "caffeinated": use_caffeinate,
            "log": log_path,
            "pid_file": pid_path,
            "results_dir": results_dir,
            "monitor": "poll journal/run.json + windows.jsonl; recover with run_window.py --resume",
        }
    ))


if __name__ == "__main__":
    main()
