"""Shared test environment.

Keeps tests away from the repository's config template and the user's real
FreeDATA app directory: every test run gets its own temporary config file and
message database. Import this module before creating an AppContext.
"""

import atexit
import configparser
import os
import shutil
import tempfile

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_TEMPLATE = os.path.join(_REPO_DIR, "freedata_server", "config.ini.example")

_TMP_DIR = tempfile.mkdtemp(prefix="freedata-tests-")
atexit.register(shutil.rmtree, _TMP_DIR, ignore_errors=True)

os.environ.setdefault("FREEDATA_DATABASE", os.path.join(_TMP_DIR, "freedata-messages.db"))


def config_path():
    """Return the path of a temporary copy of config.ini.example."""
    path = os.path.join(_TMP_DIR, "config.ini")
    if not os.path.exists(path):
        shutil.copyfile(CONFIG_TEMPLATE, path)
        # the tests cover the FSK modes too, which are off by default
        parser = configparser.ConfigParser()
        parser.read(path)
        parser["MODEM"]["enable_fsk"] = "True"
        with open(path, "w") as f:
            parser.write(f)
    return path


def patch_threading_event(module_name, event):
    """Patch threading.Event as seen from one module only, so it returns event.

    mock.patch("freedata_server.x.threading.Event") would patch the threading
    module itself, and every thread still running in the test process (such as
    the decoding threads of earlier tests, which call threading.Event().wait())
    would then use up the mock's answers. This replaces the module's own
    threading name with a copy whose Event returns event.
    """
    import importlib
    import threading
    import types
    from unittest import mock

    module = importlib.import_module(module_name)
    local = types.SimpleNamespace(**{
        name: getattr(threading, name) for name in dir(threading) if not name.startswith("__")
    })
    local.Event = mock.Mock(return_value=event)
    return mock.patch.object(module, "threading", local)
