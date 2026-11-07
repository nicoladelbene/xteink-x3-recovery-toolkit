"""The README quick start must keep working: run the synthetic failing-card demo."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_simulated_failing_card_demo(tmp_path):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "examples", "simulate_failing_card.py"),
                        str(tmp_path / "demo")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "FAIL  read1/" in out                  # read 1 alone is damaged
    assert "FAIL  voted/" in out                  # the majority vote does not fix it
    assert "byte-identical to the original" in out
