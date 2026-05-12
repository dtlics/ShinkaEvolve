from shinka.llm.constants import (
    BACKOFF_MAX_TIME,
    BACKOFF_MAX_TRIES,
    BACKOFF_MAX_VALUE,
    TIMEOUT,
)
from shinka.llm.providers.openai import MAX_TIME as OPENAI_MAX_TIME
from shinka.llm.providers.openai import MAX_TRIES as OPENAI_MAX_TRIES
from shinka.llm.providers.openai import MAX_VALUE as OPENAI_MAX_VALUE


def test_llm_backoff_max_time_tracks_timeout():
    expected = TIMEOUT * 5

    assert BACKOFF_MAX_TIME == expected
    assert OPENAI_MAX_TIME == expected


def test_llm_backoff_retry_constants_are_shared():
    assert OPENAI_MAX_TRIES == BACKOFF_MAX_TRIES
    assert OPENAI_MAX_VALUE == BACKOFF_MAX_VALUE
