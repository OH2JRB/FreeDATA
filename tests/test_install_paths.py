"""The bundled lib/ and data/ files are found from the install, in a Nuitka
build and from source, whatever the working directory is."""

import os
import platform
import tempfile
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import helpers, install_paths

PACKAGE_DIR = os.path.dirname(os.path.abspath(install_paths.__file__))
EXE = ".exe" if platform.system() == "Windows" else ""


class compiled:
    """Pretends install_paths was compiled by Nuitka."""

    def __enter__(self):
        install_paths.__compiled__ = object()

    def __exit__(self, *exc):
        del install_paths.__compiled__


class TestBaseDir(unittest.TestCase):
    def test_source_uses_package_dir(self):
        self.assertEqual(install_paths.base_dir(), PACKAGE_DIR)
        self.assertEqual(install_paths.lib_dir("codec2"), os.path.join(PACKAGE_DIR, "lib", "codec2"))
        self.assertTrue(os.path.isdir(install_paths.lib_dir("codec2")))

    def test_nuitka_build_uses_dist_dir(self):
        # modules in <dist>/freedata_server/, --include-data-dir=lib=lib files in <dist>/lib/
        with compiled():
            self.assertEqual(install_paths.base_dir(), os.path.dirname(PACKAGE_DIR))
            self.assertEqual(
                install_paths.lib_dir("hamlib", "bin"),
                os.path.join(os.path.dirname(PACKAGE_DIR), "lib", "hamlib", "bin"),
            )

    def test_independent_of_cwd(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                self.assertEqual(install_paths.base_dir(), PACKAGE_DIR)
                with compiled():
                    self.assertEqual(install_paths.base_dir(), os.path.dirname(PACKAGE_DIR))
            finally:
                os.chdir(cwd)


class TestFindBinaryPaths(unittest.TestCase):
    def make_install(self, tmp):
        """A fake build: <tmp>/dist/freedata_server/ and <tmp>/dist/lib/hamlib/bin/rigctld."""
        package_dir = os.path.join(tmp, "dist", "freedata_server")
        bin_dir = os.path.join(tmp, "dist", "lib", "hamlib", "bin")
        os.makedirs(package_dir)
        os.makedirs(bin_dir)
        rigctld = os.path.join(bin_dir, "rigctld" + EXE)
        open(rigctld, "w").close()
        return package_dir, rigctld

    def test_bundled_rigctld_found_from_any_cwd(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as elsewhere:
            package_dir, rigctld = self.make_install(tmp)
            # a rigctld under the working directory is not the bundled one and is not searched
            open(os.path.join(elsewhere, "rigctld" + EXE), "w").close()
            os.chdir(elsewhere)
            try:
                with mock.patch.object(install_paths, "PACKAGE_DIR", package_dir), compiled():
                    self.assertEqual(helpers.find_binary_paths("rigctld"), [rigctld])
            finally:
                os.chdir(cwd)

    def test_bundled_rigctld_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_dir, rigctld = self.make_install(tmp)
            with mock.patch.object(install_paths, "PACKAGE_DIR", package_dir), compiled():
                paths = helpers.find_binary_paths("rigctld", search_system_wide=True)
        self.assertEqual(paths[0], rigctld)

    def test_no_bundled_rigctld(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(install_paths, "PACKAGE_DIR", os.path.join(tmp, "freedata_server")):
                self.assertEqual(helpers.find_binary_paths("rigctld"), [])


if __name__ == "__main__":
    unittest.main()
