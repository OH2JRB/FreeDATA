"""When libcodec2 does not load, the server stops while it starts with its own
exit code (EXIT_CODEC2_NOT_LOADED) and says why on stderr. Each case imports
freedata_server.codec2 in a new Python process, as the server does."""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

from freedata_server.constants import EXIT_CODEC2_NOT_LOADED

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def import_codec2(setup="", after="", cwd=REPO_DIR):
    """Runs setup, then imports freedata_server.codec2 in a new process, then runs after."""
    env = dict(os.environ, PYTHONPATH=REPO_DIR)
    return subprocess.run(
        [sys.executable, "-c", f"{setup}\nimport freedata_server.codec2\n{after}"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestCodec2Load(unittest.TestCase):
    def test_loads(self):
        result = import_codec2()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_loads_from_package_lib_from_any_cwd(self):
        codec2_dir = os.path.join(REPO_DIR, "freedata_server", "lib", "codec2")
        with tempfile.TemporaryDirectory() as tmp:
            result = import_codec2(after="print(freedata_server.codec2.codec2_dir)", cwd=tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], codec2_dir)

    def test_nuitka_build_searches_dist_lib(self):
        # modules in <dist>/freedata_server/, --include-data-dir=lib=lib files in <dist>/lib/:
        # the library is looked for only in <dist>/lib/codec2/, here the repository's (missing) lib/
        setup = "from freedata_server import install_paths\ninstall_paths.__compiled__ = object()"
        result = import_codec2(setup)
        self.assertEqual(result.returncode, EXIT_CODEC2_NOT_LOADED)
        self.assertIn(f"no libcodec2 library found under {os.path.join(REPO_DIR, 'lib', 'codec2')}", result.stderr)

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
