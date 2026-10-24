"""
Lazy access to sounddevice / PortAudio.

Importing sounddevice initializes PortAudio and raises if that fails, e.g. when
libportaudio is missing or no sound server is running (PulseAudio/PipeWire on
Linux). The server must keep running in that case: the API and GUI stay
available and only the modem can't start. So sounddevice is imported on first
use, and a failed import is retried on the next call (a sound server may have
started in the meantime).
"""

import threading

import structlog

log = structlog.get_logger("audio")


class AudioUnavailableError(Exception):
    """Raised when sounddevice / PortAudio can't be loaded."""


_lock = threading.Lock()
_sounddevice = None
_last_error = None


def get_sounddevice():
    """Returns the sounddevice module, importing it if needed.

    Raises:
        AudioUnavailableError: If PortAudio can't be loaded or initialized.
    """
    global _sounddevice, _last_error
    with _lock:
        if _sounddevice is None:
            try:
                import sounddevice
            except Exception as e:  # OSError (no library) or sounddevice.PortAudioError
                if str(e) != _last_error:
                    log.warning("[AUD] Audio not available, the modem can't start", e=str(e))
                _last_error = str(e)
                raise AudioUnavailableError(f"Audio not available: {e}") from e
            _sounddevice = sounddevice
            _last_error = None
        return _sounddevice


def loaded_sounddevice():
    """Returns the sounddevice module if it has been loaded, otherwise None.
    Never tries to load it (for shutdown and cleanup code)."""
    return _sounddevice


def last_error():
    """The error of the last failed load attempt, or None."""
    return _last_error
