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
import uvicorn

log = structlog.get_logger("shutdown_guard")


class Server(uvicorn.Server):
    """uvicorn's Server, but a second SIGINT within DUPLICATE_SIGINT_SECONDS of
    the first does not force the exit.

    uvicorn takes a second SIGINT as "force quit": it skips the lifespan
    shutdown, so FreeDATA's own shutdown (AppContext.shutdown: sessions,
    scheduler, modem, radio) never runs, and the non-daemon scheduler thread
    then holds the process until exit_if_threads_hang. Ctrl-C in a terminal
    sends one SIGINT to the whole process group, and launchers forward it as
    well (run-freedata-linux.sh's trap), so one Ctrl-C arrives twice. On the air
    on 2026-11-12 (tests S1 and S2) both stops went this way. A Ctrl-C pressed
    again later still forces the exit.
    """

    DUPLICATE_SIGINT_SECONDS = 2.0

    first_exit_signal_time = None

    def handle_exit(self, sig, frame):
        now = time.monotonic()
        if (
            sig == signal.SIGINT
            and self.should_exit
            and self.first_exit_signal_time is not None
            and now - self.first_exit_signal_time < self.DUPLICATE_SIGINT_SECONDS
        ):
            log.info("[SHUTDOWN] Second stop signal right after the first, shutting down normally")
            return
        if not self.should_exit:
            self.first_exit_signal_time = now
        super().handle_exit(sig, frame)


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
