# Per-call HTTP timeout on the OpenAI / Azure OpenAI SDK clients themselves.
# In bg+poll mode the long wait happens in ``responses.retrieve``, not in a
# single long-running HTTP call, so this only needs to cover the initial
# create and each retrieve round-trip.
TIMEOUT = 1200

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
