import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_config_reference_is_up_to_date():
    r = subprocess.run([sys.executable, "scripts/gen_config_reference.py", "--check"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
