"""Shared test environment.

Keeps tests away from the repository's config template and the user's real
FreeDATA app directory: every test run gets its own temporary config file and
message database. Import this module before creating an AppContext.
"""

import atexit
import configparser
import os
import shutil
import sys
import tempfile

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_TEMPLATE = os.path.join(_REPO_DIR, "freedata_server", "config.ini.example")

_TMP_DIR = tempfile.mkdtemp(prefix="freedata-tests-")


def _remove_tmp_dir():
    # an open SQLite database cannot be deleted on Windows: close the shared
    # engines first (a test may have left them open), if they were ever used
    db_manager = sys.modules.get("freedata_server.message_system_db_manager")
    if db_manager is not None:
        db_manager.dispose_engines()
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


atexit.register(_remove_tmp_dir)

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


def start(ctx, timeout=30):
    """Run ctx.startup() and wait until the modem is running.

    startup() only queues the modem start; the service manager's runner thread
    creates the RF modem and the frame dispatcher a moment later. A test that
    hands frames to service_manager.frame_dispatcher straight away can get
    there first, so wait for is_modem_running, which is set after both exist.
    """
    import time

    ctx.startup()
    deadline = time.monotonic() + timeout
    while not ctx.state_manager.is_modem_running:
        if time.monotonic() > deadline:
            raise TimeoutError(f"modem did not start within {timeout} s")
        time.sleep(0.01)


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
