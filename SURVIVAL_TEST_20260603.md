# Background-job survival test — 2026-06-03

> **⚠️ SUPERSEDED BY ROUND 3 (see bottom): the reaper WAS reproduced** on a genuine ~16-min
> UNINTERRUPTED dormancy. Rounds 1–2 below did NOT reproduce it only because periodic alarm
> wake-ups kept resetting the session-dormancy clock so it never crossed the reaper threshold.
> The session-tied `run_in_background` job was KILLED at ~16 min idle; the detached daemon survived.

**Goal:** reproduce "the reaper kills my background jobs when the main-loop session goes idle
(sometimes <60s)," reported by a live orchestrator agent (confirmed main-loop, not subagent).

## Method
A 2s heartbeat (timestamped to a log so the death time is pinpointed to ±2s) launched three ways
from THIS top-level main-loop session, then the session was taken idle (turn ended) for two
windows (180s, then 600s continuous; ~16 min total):
- **Job A** — Bash `run_in_background` (the harness-tracked mechanism the orchestrator uses). Process
  tree: heartbeat `73073` ← zsh `73071` ← `claude …` `30870` — i.e. a session-tied child.
- **Job B** — OS-detached daemon via Python `start_new_session=True` (the old `run_detached` pattern).
  Reparented to **ppid 1 / launchd**, own session.
- **Job C/C2** — `run_in_background` `sleep` alarms (180s, 600s) — also silent test subjects + wake source.

## Result: COULD NOT REPRODUCE — zero reaping in this session
| Job | Mechanism | Beats | Span | Max gap | Outcome |
|---|---|---|---|---|---|
| A | run_in_background (session-tied) | 529 | ~16 min | 3s (sleep jitter) | continuous, survived; only died when I `pkill`ed it (exit 144) |
| B | detached daemon (ppid 1) | 545 | ~16 min | 3s | continuous, survived |
| C / C2 | silent `sleep` alarms | — | 180s / 600s | — | both completed exit 0 |

No gaps, no pauses, no kills — for the session-tied job, the detached daemon, AND silent sleepers.

## Environment (this session — the NON-reaping one)
- **Power: BATTERY** (100%, discharging) — so battery alone is NOT the trigger.
- macOS 26.3.1; **Claude Code 2.1.160**, model opus-4-8, **`--allow-dangerously-skip-permissions`** + `--permission-mode auto`.
- Bash runs as **direct children of the persistent `claude` process** — **no `sandbox-exec`/seatbelt wrapper** in the job's process tree (the only seatbelt processes were unrelated apps: GitHub Desktop, Discord).

## Conclusion — it's the Bash execution mode (sandbox), not idle/battery/subagent
The reaping is **environment-specific**, and the operative variable is **how Bash is executed**:
- **This session:** Bash children are persistent (orphaned under the still-alive `claude` process across
  turns) → they survive idle indefinitely. Ruled OUT here: idle duration (≥16 min fine), battery (on
  batt, fine), main-loop-vs-subagent (main loop, fine).
- **The reaping environment** (the other agent's) must run Bash in an **ephemeral/sandboxed context that
  is torn down when the session goes idle** — which reclaims the bg jobs inside it. That matches the
  agent's own words: "the sandbox that runs commands is ephemeral — it reclaims detached background jobs
  when the session goes idle." The likely differentiator is sandbox/permission mode (this session has
  `--allow-dangerously-skip-permissions`, which runs Bash un-sandboxed).

This **corrects** the earlier root-cause audit's "it was the subagent vehicle" conclusion (the user
confirmed main-loop, and a main-loop session reproduces nothing here) — that audit over-relied on a 60s
experiment. The "subagent kills its own bg jobs" memory note may still be true as a *separate* mechanism,
but it is NOT what the live main-loop failure was.

## What this validates for the fix
- **The detached daemon survives regardless** (Job B, ppid 1) — so re-adding the `run_detached.py` pattern
  (`start_new_session=True` + journal-poll + `--resume`) is the **bulletproof, environment-agnostic** fix
  if the orchestrator must run in a reaping (sandboxed) session.
- **If the orchestrator runs un-sandboxed** (this session's mode), bg jobs survive idle and the auto-wake
  model works as-is — no daemon needed.

## Recommendation
1. Identify the failing session's Bash mode. If it's sandboxed, run the orchestrator in the **un-sandboxed
   mode** (the mode tested here, where bg jobs persist) — likely the simplest fix.
2. If sandboxed mode is required, use the **detached daemon** (validated here) — accept poll-to-wake.
3. Keep **relaunch-on-every-notification (killed == relaunch `--resume`) + a safety-net wakeup** as
   defense-in-depth in either case.

Raw logs: `/Users/dantongli/shinka_reaper_test/{jobA,jobB,checkpoint}.log` + `manifest.txt`.

---

## ROUND 3 — REPRODUCED (genuine uninterrupted dormancy)

**Method fix:** no alarm jobs this round. Spawned the subject, ended the turn, and stayed
genuinely dormant for a single long stretch (manual wake). A detached **watchdog** (ppid 1)
logged the subject's liveness every 3s + power/system-idle every 30s.

- **Job A** — `run_in_background` heartbeat (the real orchestrator mechanism), session-tied
  (`heartbeat ← zsh ← claude proc`).
- **Job B** — detached watchdog daemon (`start_new_session`, **ppid 1**), the witness + control.

**Result (all on battery):**
- Session went dormant ~**15:21:19Z**.
- **Job A REAPED at ~15:37:44–53Z** — i.e. **~16 minutes into dormancy** (watchdog logged
  `*** A transition 1->0 ***`). Job A ran fine for 327 beats then was killed (`run_in_background`
  status = `killed`).
- **Job B (detached, ppid 1) SURVIVED** continuously to its natural 40-min completion (~16:01Z).
- **Not OS-idle-sleep:** at the kill, HID idle had just reset to ~0.2s (recent user input at
  15:37:32Z) and power was battery throughout → the trigger is **Claude-session dormancy /
  sandbox reclaim**, NOT display/idle sleep. `caffeinate` is irrelevant.
- **Wake failed:** the `killed` notification did NOT re-invoke the dormant session; it surfaced
  only on a MANUAL wake ~80 min later. A reaped/dormant session can't be woken by a bg-job
  notification.

**Confirmed root issue:** the orchestrator's "deploy `run_window` (run_in_background) → sleep →
woken on exit" loop fails on BOTH axes in a reaping session — the job is killed mid-cluster AND
the wake can't fire. This corrects the root-cause audit's "subagent" conclusion (this is a MAIN
LOOP) and the Rounds 1–2 "couldn't reproduce" above. The reaper threshold is variable (~16 min
here; <60s reported elsewhere) but the mechanism is confirmed.

**Validated for the fix:** the detached daemon (ppid 1) is immune to the reaper — BUT a detached
daemon CANNOT wake the attached session (only harness-tracked jobs re-invoke the main loop, and
those get reaped + can't wake a dormant session). So neither mechanism alone is sufficient:
- `run_in_background` → auto-wakes, but reaped on dormancy + can't wake a dormant session.
- detached daemon → survives, but can't auto-wake (needs manual poll).

**Fix direction:** prevent the session from ever being dormant past the reaper threshold — keep
the agent re-invoked on a short cadence (short bg "tick" jobs that complete well under the
threshold → wake → relaunch), and run the heavy `run_window` either as bounded SHORT batches
(each finishing before the threshold) or as a detached daemon whose progress the short ticks
poll from the journal. Pair with a safety-net scheduled wakeup. (Round-3 raw logs:
`/Users/dantongli/shinka_reaper_test/round3/{jobA,jobB}.log`.)
