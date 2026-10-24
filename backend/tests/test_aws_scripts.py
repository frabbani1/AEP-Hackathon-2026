"""Tests for the AWS helper scripts that do not need AWS (fake table, dry run)."""
import importlib.util
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_module():
    spec = importlib.util.spec_from_file_location("load_dynamodb", ROOT / "scripts" / "load_dynamodb.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ROWS = [
    {"t": "p_0000000000000001", "st": "ok", "why": None, "R": 1.5, "Re": 0.25, "svc": "Overhead", "P": 2, "H": 3, "n": 7, "c": "Medium"},
    {"t": "p_0000000000000002", "st": "fb", "why": "circuit_too_small"},
]


def test_to_item_uses_token_key_drops_none_and_uses_decimal():
    m = load_module()
    item = m.to_item(ROWS[0])
    assert item["token"] == "p_0000000000000001" and "t" not in item and "why" not in item
    assert item["R"] == Decimal("1.5") and isinstance(item["R"], Decimal) and item["P"] == 2
    assert m.to_item(ROWS[1]) == {"token": "p_0000000000000002", "st": "fb", "why": "circuit_too_small"}


class FakeBatch:
    def __init__(self, sink): self.sink = sink
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def put_item(self, Item): self.sink.append(Item)


class FakeTable:
    def __init__(self): self.items, self.kwargs = [], None
    def batch_writer(self, **kw):
        self.kwargs = kw
        return FakeBatch(self.items)


def test_write_items_batches_every_item():
    m, t = load_module(), FakeTable()
    n = m.write_items(t, (m.to_item(r) for r in ROWS))
    assert n == 2 and len(t.items) == 2 and t.kwargs == {"overwrite_by_pkeys": ["token"]}


def test_dry_run_never_imports_boto3(tmp_path):
    f = tmp_path / "p.jsonl"
    f.write_text("".join(json.dumps(r) + "\n" for r in ROWS))
    out = subprocess.run([sys.executable, str(ROOT / "scripts/load_dynamodb.py"), "--table", "t", "--file", str(f)],
                         capture_output=True, text=True, check=True)
    assert "DRY RUN: would write 2 items" in out.stdout and "no AWS calls made" in out.stdout


def test_publish_script_refuses_to_run_without_config():
    out = subprocess.run(["bash", str(ROOT / "scripts/aws_publish.sh")], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin"})
    assert out.returncode != 0 and "DATA_BUCKET" in out.stderr
