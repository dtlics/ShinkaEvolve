import os

TIMEOUT = 3600  # 60 min — the long TOTAL/blocking ceiling (a background job may legitimately
# think this long); gpt-5.4-pro at reasoning_effort=high can think >20 min. NOT the per-request cap.

# SHORT per-HTTP-request cap for the background submit/status-poll calls, distinct from the long
# total-job wall above. A status GET returns in <1s; 60s tolerates a slow network hop. The bg poll
# loops (orchestrator/scripts/_azure.py, shinka/llm/agent/background_model.py, .../dr_client.py)
# wrap each create()/retrieve() in asyncio.wait_for(PER_REQUEST_TIMEOUT) and RETRY a timed-out
# status GET, so a single hung request can no longer ride the whole job wall. Override via
# SHINKA_BG_HTTP_TIMEOUT_SEC.
PER_REQUEST_TIMEOUT = float(os.environ.get("SHINKA_BG_HTTP_TIMEOUT_SEC", "60"))
