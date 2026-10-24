"""Real clipboard round trip. Opt-in because it overwrites the clipboard (the original is restored).

Run with:  JOBHELPER_TEST_CLIPBOARD=1 python -m pytest tests/test_clipboard.py
"""
import os
import subprocess
import sys

import pytest

from jobhelper.browser.clipboard import read_clipboard, write_clipboard

pytestmark = pytest.mark.skipif(os.environ.get("JOBHELPER_TEST_CLIPBOARD") != "1",
                                reason="set JOBHELPER_TEST_CLIPBOARD=1 to run (overwrites the clipboard)")


@pytest.fixture()
def keep_clipboard():
    original = read_clipboard()
    yield
    write_clipboard(original)


def test_round_trip_including_unicode(keep_clipboard):
    text = "https://example.com/jobs/123?q=café ✓ \U0001F680\nsecond line"
    write_clipboard(text)
    assert read_clipboard() == text


@pytest.mark.skipif(sys.platform != "win32", reason="uses PowerShell")
def test_other_programs_can_read_what_we_wrote_without_waiting(keep_clipboard):
    """Regression: tkinter's delayed clipboard made other programs hang or read nothing."""
    write_clipboard("https://example.com/jobs/456")
    result = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                            capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
    assert result.stdout.decode("utf-8", "replace").strip() == "https://example.com/jobs/456"
