"""Last-resort guard for a server exit that would hang.

The server shuts down normally, so atexit handlers, log flushing and database
engine disposal run. Its own threads are daemon threads or joined on
shutdown, but a thread that still blocks (a stuck driver call, a library
thread) would keep the interpreter alive forever. This guard gives such
threads a few seconds, then names them in the log and exits hard.
"""

import logging
import os
import signal
import threading
import time

import structlog

log = structlog.get_logger("shutdown_guard")


def _raise_keyboard_interrupt(signum, frame):
    raise KeyboardInterrupt


def stop_signals_raise_keyboard_interrupt():
    """Lets SIGTERM (and SIGBREAK on Windows) raise KeyboardInterrupt, as
    SIGINT does.

    After its own shutdown uvicorn restores the handlers it found and raises
    the stop signal again. With the default handlers SIGTERM and SIGBREAK
    would end the process at once, without atexit handlers or log flushing.
    """
    for name in ("SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _raise_keyboard_interrupt)


def stuck_threads(timeout=3.0):
    """Joins the live non-daemon threads other than the main thread, within
    timeout seconds in total, and returns those still alive."""
    deadline = time.monotonic() + timeout
    own = (threading.main_thread(), threading.current_thread())
    for thread in threading.enumerate():
        if thread not in own and not thread.daemon:
            thread.join(max(0.0, deadline - time.monotonic()))
    return [t for t in threading.enumerate() if t not in own and not t.daemon]


def exit_if_threads_hang(timeout=3.0):
    """Returns when no non-daemon thread outlives timeout; otherwise logs the
    names of the stuck threads, flushes the logs and exits with code 1."""
    stuck = stuck_threads(timeout)
    if not stuck:
        return
    log.warning("[SHUTDOWN] threads still running, forcing exit", threads=[t.name for t in stuck])
    logging.shutdown()
    os._exit(1)
