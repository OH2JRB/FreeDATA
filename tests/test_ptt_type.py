"""RADIO.ptt_type values that Hamlib does not know: FreeDATA's default was
"USB" and the GUI offered "MICDATA", which rigctld took as NONE ("Unrecognised
PTT type, using NONE"), so the T command never keyed the radio. They become
RIG, in the config file when it is loaded and in rigctld's arguments."""

import configparser
import os
import shutil
import tempfile
import types
import unittest

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import rigctld
from freedata_server.config import CONFIG, normalize_ptt_type


class TestNormalize(unittest.TestCase):
    def test_unknown_types_become_rig(self):
        for value in ("USB", "usb", "MICDATA"):
            self.assertEqual(normalize_ptt_type(value), "RIG")

    def test_hamlib_types_stay(self):
        for value in ("RIG", "DTR", "RTS", "PARALLEL", "CM108", "GPIO", "GPION", "NONE", "ignore"):
            self.assertEqual(normalize_ptt_type(value), value)


class TestConfigFile(unittest.TestCase):
    def test_usb_in_an_older_config_becomes_rig(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "config.ini")
        shutil.copyfile(testenv.CONFIG_TEMPLATE, path)
        parser = configparser.ConfigParser()
        parser.read(path)
        parser["RADIO"]["ptt_type"] = "USB"
        with open(path, "w") as f:
            parser.write(f)
        self.assertEqual(CONFIG(None, path).read()["RADIO"]["ptt_type"], "RIG")
        saved = configparser.ConfigParser()
        saved.read(path)
        self.assertEqual(saved["RADIO"]["ptt_type"], "RIG")

    def test_template_default_is_rig(self):
        parser = configparser.ConfigParser()
        parser.read(testenv.CONFIG_TEMPLATE)
        self.assertEqual(parser["RADIO"]["ptt_type"], "RIG")


class TestRigctldArguments(unittest.TestCase):
    def args(self, ptt_type):
        radio = rigctld.radio.__new__(rigctld.radio)
        radio.ctx = types.SimpleNamespace(
            config_manager=types.SimpleNamespace(
                config={
                    "RADIO": {
                        "model_id": 3073,
                        "serial_port": "/dev/ttyUSB0",
                        "serial_speed": 19200,
                        "ptt_port": "ignore",
                        "ptt_type": ptt_type,
                        "serial_dcd": "NONE",
                        "serial_dtr": "OFF",
                        "serial_rts": "OFF",
                        "data_bits": 8,
                        "stop_bits": 1,
                    },
                    "RIGCTLD": {"arguments": "", "enable_vfo": False},
                }
            )
        )
        args = radio.format_rigctld_args()
        return args[args.index("-P") + 1] if "-P" in args else None

    def test_usb_is_passed_as_rig(self):
        self.assertEqual(self.args("USB"), "RIG")

    def test_rig_and_dtr_unchanged(self):
        self.assertEqual(self.args("RIG"), "RIG")
        self.assertEqual(self.args("DTR"), "DTR")


if __name__ == "__main__":
    unittest.main()
