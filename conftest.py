"""Root conftest: ensure worktree src is first in sys.path."""
import sys
from pathlib import Path

# Keep the worktree's src ahead of any other whisper_aloud installation
# so that editable-install path resets don't affect test results.
_wt_src = str(Path(__file__).parent / "src")
if _wt_src not in sys.path:
    sys.path.insert(0, _wt_src)
