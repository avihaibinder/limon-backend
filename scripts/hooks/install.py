#!/usr/bin/env python3
"""Install the repo's git hooks (currently just pre-push) into .git/hooks.

Usage: uv run python scripts/hooks/install.py
"""

import shutil
import stat
import subprocess
from pathlib import Path

HOOKS = ["pre-push"]


def main() -> None:
    repo_root = Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    )
    source_dir = repo_root / "scripts" / "hooks"
    target_dir = repo_root / ".git" / "hooks"

    for hook in HOOKS:
        source = source_dir / hook
        target = target_dir / hook
        shutil.copyfile(source, target)
        target.chmod(target.stat().st_mode | stat.S_IEXEC)
        print(f"Installed {hook} -> {target}")


if __name__ == "__main__":
    main()
