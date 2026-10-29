"""Where the files shipped with the server (lib/, data/) are installed.

From source or a pip install, lib/ and data/ are in the freedata_server
package directory. A Nuitka standalone build puts the package modules in
<dist>/freedata_server/ but the --include-data-dir=lib=lib and data=data files
in <dist>/lib/ and <dist>/data/, so there the base is one level up.

__compiled__.containing_dir cannot be used for this: it is the directory that
holds the .dist folder, one level above <dist>, meant for files next to the
build rather than inside it. See
https://nuitka.net/user-documentation/common-issue-solutions.html#standalone-finding-files
"""

import os

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def base_dir():
    """Directory that holds lib/ and data/."""
    try:
        __compiled__  # type: ignore[name-defined]  # noqa: B018
    except NameError:
        return PACKAGE_DIR
    return os.path.dirname(PACKAGE_DIR)


def lib_dir(*parts):
    """Path inside the bundled lib/ directory."""
    return os.path.join(base_dir(), "lib", *parts)


def data_dir(*parts):
    """Path inside the bundled data/ directory."""
    return os.path.join(base_dir(), "data", *parts)
