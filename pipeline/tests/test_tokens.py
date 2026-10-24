import hashlib
import hmac
import logging

import pytest

from aep_pipeline.errors import ConfigError
from aep_pipeline.tokens import KEY_ENV_VAR, MIN_KEY_LENGTH, Tokenizer, load_key

KEY_A = b"first-secret-key-0123456789"
KEY_B = b"second-secret-key-9876543210"


def test_same_input_and_key_give_the_same_token():
    assert Tokenizer(KEY_A).token("premise", "900000001") == Tokenizer(KEY_A).token("premise", "900000001")


def test_different_key_gives_a_different_token():
    assert Tokenizer(KEY_A).token("premise", "900000001") != Tokenizer(KEY_B).token("premise", "900000001")


def test_different_values_and_domains_give_different_tokens():
    tok = Tokenizer(KEY_A)
    assert tok.token("premise", "1") != tok.token("premise", "2")
    assert tok.token("premise", "1") != tok.token("device", "1")


def test_token_is_128_bit_hex_and_matches_the_documented_construction():
    token = Tokenizer(KEY_A).token("premise", "900000001")
    assert len(token) == 32 and int(token, 16) >= 0
    expected = hmac.new(KEY_A, "premise\x1f900000001".encode(), hashlib.sha256).digest()[:16].hex()
    assert token == expected


def test_token_does_not_reveal_the_value_or_the_key():
    tok = Tokenizer(KEY_A)
    token = tok.token("premise", "900000001")
    assert "900000001" not in token
    assert KEY_A.decode() not in token
    assert KEY_A.decode() not in tok.fingerprint()
    assert KEY_A.decode() not in repr(tok) and KEY_A.decode() not in str(tok)


def test_fingerprint_is_stable_short_and_differs_by_key():
    assert Tokenizer(KEY_A).fingerprint() == Tokenizer(KEY_A).fingerprint()
    assert Tokenizer(KEY_A).fingerprint() != Tokenizer(KEY_B).fingerprint()
    assert len(Tokenizer(KEY_A).fingerprint()) == 12


def test_key_never_appears_in_logs_or_output(caplog, capsys):
    with caplog.at_level(logging.DEBUG):
        tok = Tokenizer(KEY_A)
        for value in range(50):
            tok.token("premise", value)
        tok.fingerprint()
    captured = capsys.readouterr()
    assert KEY_A.decode() not in caplog.text
    assert KEY_A.decode() not in captured.out + captured.err


def test_load_key_requires_the_environment_variable():
    with pytest.raises(ConfigError) as error:
        load_key({})
    assert KEY_ENV_VAR in str(error.value)


def test_load_key_rejects_short_keys_without_echoing_them():
    short = "x" * (MIN_KEY_LENGTH - 1)
    with pytest.raises(ConfigError) as error:
        load_key({KEY_ENV_VAR: short})
    assert short not in str(error.value)


def test_load_key_returns_bytes():
    assert load_key({KEY_ENV_VAR: "k" * MIN_KEY_LENGTH}) == b"k" * MIN_KEY_LENGTH
