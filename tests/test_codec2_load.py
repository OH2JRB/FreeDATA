"""When libcodec2 does not load, the server stops while it starts with its own
exit code (EXIT_CODEC2_NOT_LOADED) and says why on stderr. Each case imports
freedata_server.codec2 in a new Python process, as the server does."""

import os
import subprocess
import sys
import textwrap
import unittest

from freedata_server.constants import EXIT_CODEC2_NOT_LOADED

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def import_codec2(setup=""):
    """Runs setup, then imports freedata_server.codec2 in a new process."""
    env = dict(os.environ, PYTHONPATH=REPO_DIR)
    return subprocess.run(
        [sys.executable, "-c", f"{setup}\nimport freedata_server.codec2"],
        cwd=REPO_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestCodec2Load(unittest.TestCase):
    def test_loads(self):
        result = import_codec2()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_library_does_not_load(self):
        setup = textwrap.dedent("""
            import ctypes
            def fail(path, *args, **kwargs):
                raise OSError("wrong ELF class")
            ctypes.CDLL = fail
        """)
        result = import_codec2(setup)
        self.assertEqual(result.returncode, EXIT_CODEC2_NOT_LOADED)
        self.assertIn(f"Libcodec2 not loaded - exiting with code {EXIT_CODEC2_NOT_LOADED}", result.stderr)
        # each library tried, with the reason it did not load
        self.assertRegex(result.stderr, r"libcodec2\S*: wrong ELF class")

    def test_library_not_found(self):
        result = import_codec2("import glob\nglob.glob = lambda *args, **kwargs: []")
        self.assertEqual(result.returncode, EXIT_CODEC2_NOT_LOADED)
        self.assertIn("no libcodec2 library found under", result.stderr)

    def test_exit_code_is_its_own(self):
        # 0 is a normal exit and 1 the other startup and shutdown failures
        self.assertNotIn(EXIT_CODEC2_NOT_LOADED, (0, 1))


if __name__ == "__main__":
    unittest.main()
