"""The server must keep running when PortAudio can't be loaded or initialized
(no libportaudio, no sound server): only the modem fails to start.

The checks run in a fresh interpreter where importing sounddevice fails, like
it does on a machine without audio."""

import os
import subprocess
import sys
import unittest

import testenv

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = r"""
import os, sys
sys.modules["sounddevice"] = None  # makes "import sounddevice" fail

import freedata_server.server as server  # must not raise
from freedata_server import audio, audio_backend

assert audio.get_audio_devices() == ([], []), "no devices without audio"
assert audio.test_audio_devices("abcd", "abcd") == [False, False]
assert audio.get_device_index_from_crc("abcd", True) is None
assert audio_backend.last_error() is not None
try:
    audio_backend.get_sounddevice()
    raise AssertionError("expected AudioUnavailableError")
except audio_backend.AudioUnavailableError:
    pass
audio.terminate()  # nothing loaded, must not raise

server.ctx.config_manager.config["STATION"]["mycall"] = "AA1AAA"
assert server.ctx.service_manager.start_modem() is False, "modem can't start without audio"
assert server.ctx.rf_modem is None

# audio comes back (e.g. the sound server was started): the next call loads it
del sys.modules["sounddevice"]
try:
    import sounddevice  # noqa: F401
    available = True
except Exception:
    available = False  # no audio on this machine either, can't check recovery
    sys.modules.pop("sounddevice", None)
if available:
    assert audio_backend.get_sounddevice() is sys.modules["sounddevice"]
    assert audio_backend.last_error() is None

print("AUDIO-BACKEND-OK", flush=True)
os._exit(0)
"""


class TestServerWithoutAudio(unittest.TestCase):
    def test_server_starts_without_portaudio(self):
        env = os.environ.copy()
        env["FREEDATA_CONFIG"] = testenv.config_path()
        env["PYTHONPATH"] = REPO_DIR + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [sys.executable, "-c", SCRIPT],
            cwd=REPO_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output[-3000:])
        self.assertIn("AUDIO-BACKEND-OK", result.stdout, output[-3000:])


if __name__ == "__main__":
    unittest.main()
