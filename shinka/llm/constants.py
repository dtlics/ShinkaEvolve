TIMEOUT = 1200
BACKOFF_MAX_TRIES = 20
BACKOFF_MAX_VALUE = 20
BACKOFF_MAX_TIME_MULTIPLIER = 5
BACKOFF_MAX_TIME = TIMEOUT * BACKOFF_MAX_TIME_MULTIPLIER

# --- bg+poll mode (Phase 2 of research-grounding) ---
# Polling cadence applied while waiting for a background Responses API call.
# Starts fast (1s) and grows exponentially so short calls return promptly
# but long calls don't hammer the API.
POLL_INTERVAL_INITIAL = 1.0
POLL_INTERVAL_MAX = 60.0
POLL_INTERVAL_GROWTH = 1.5

# Per-call wall-clock ceilings on the polling loop, picked by use case:
# - DEFAULT: regular proposer/meta calls
# - DR: o3-deep-research (long literature traversal)
# - SHELL_FIX: error-fix loop when shell tool is enabled (sandbox runtime)
POLL_TIMEOUT_DEFAULT = 1200  # 20 min
POLL_TIMEOUT_DR = 1800  # 30 min
POLL_TIMEOUT_SHELL_FIX = 900  # 15 min

# Bounded retries on transient errors during ``responses.retrieve(id)``.
# Polling failures don't cancel the upstream task; we just back off and
# try the retrieve again.
POLL_RETRIEVE_RETRIES = 5
