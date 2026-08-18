from __future__ import annotations

from pen.tutor import usage_snapshot


def test_usage_snapshot_is_last_call_not_a_sum() -> None:
    first = usage_snapshot(100, 20)
    second = usage_snapshot(250, 40)
    assert first == {
        "context_tokens": 100,
        "completion_tokens": 20,
        "prompt_tokens": 100,
    }
    assert second["context_tokens"] == 250
    assert second["completion_tokens"] == 40
    assert second["prompt_tokens"] == 250
    merged = {**first, **second}
    assert merged["context_tokens"] == 250
    assert merged["context_tokens"] != first["context_tokens"] + second["context_tokens"]
