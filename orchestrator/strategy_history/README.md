# strategy_history/

Append-only audit trail of every mutable strategy file ever deployed in a run.
Written by `harness/strategy_store.py`; the orchestrator never deletes from here.

Layout:

```
strategy_history/
  index.json                 # the log: list of {target, prior_hash, new_hash,
                             #   reason, window_index, prior_J, J, status, ...}
                             # status ∈ deployed | accepted | rejected | rolledback
  <hash>/                    # one dir per deployed strategy version (sha256[:16])
    <target>.py              # the snapshotted strategy file
    meta.json                # {hash, target, created_at, reason, J, window_index}
```

`index.json` is what the orchestrator greps before proposing a rewrite, so it
never re-deploys a strategy hash that already failed. Each `<hash>/` dir is what
`rollback` restores from when a deployed rewrite regresses J.
