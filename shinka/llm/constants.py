TIMEOUT = 3600  # 60 min — gpt-5.4-pro at reasoning_effort=high can think >20 min on hard problems
# Inner @backoff disabled (max_tries=1) — outer wrapper in shinka.llm.llm reconstructs the AsyncOpenAI
# client per attempt, which is the only thing that recovers from a poisoned httpx pool / sick endpoint.
BACKOFF_MAX_TRIES = 1
BACKOFF_MAX_VALUE = 20
BACKOFF_MAX_TIME = 300
