from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loopai.agents.Obtainer.datamixer import tokenizers


def test_tokenizer_count_and_encode_fall_back_on_record_errors() -> None:
    def broken_count(_: str) -> int:
        raise ValueError("unknown token")

    def broken_encode(_: str) -> list[int]:
        raise ValueError("unknown token")

    tokenizers.register("broken-for-test", broken_count, exact=True, encode_fn=broken_encode)
    tok = tokenizers.resolve("broken-for-test")

    assert tok.count("hello <|unknown|>") > 0
    assert tok.encode("hello <|unknown|>")
