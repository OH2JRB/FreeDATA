import argparse
import configparser
import datetime
import logging
import logging.config
import os
import re
import sys
import tempfile

import structlog

from freedata_server.constants import LOG_TO_TEMP_ENV_VAR

# ANSI escape sequences (CSI such as colours, OSC, and two-character escapes)
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
# remaining C0/C1 control characters and DEL; newline and tab are kept
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def strip_control_characters(text: str) -> str:
    """Remove ANSI escape sequences and control characters, keeping newline and tab."""
    return _CONTROL_CHARS.sub("", _ANSI_ESCAPE.sub("", text))


class StripControlCharsFormatter(structlog.stdlib.ProcessorFormatter):
    """Plain structlog formatter whose output carries no control characters."""

    def format(self, record: logging.LogRecord) -> str:
        return strip_control_characters(super().format(record))


def temp_log_filename(now: datetime.datetime | None = None) -> str:
    """Path of a new log file in the system temp directory, named by date and time."""
    now = now or datetime.datetime.now()
    return os.path.join(tempfile.gettempdir(), f"freedata_server_{now:%Y%m%d_%H%M%S}.log")


def log_to_temp_requested(argv: list[str]) -> bool:
    """
    True when the server should also log into a file in the temp directory:
    started with --log-to-temp, or FREEDATA_LOG_TO_TEMP set to 1/true/yes/on.
    Other arguments are left alone (uvicorn or a test runner may own them).
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--log-to-temp", action="store_true")
    args, _ = parser.parse_known_args(argv)
    env = os.getenv(LOG_TO_TEMP_ENV_VAR, "").strip().lower()
    return args.log_to_temp or env in ("1", "true", "yes", "on")


# The temp-directory log file, if one is open (set_temp_logging)
_temp_handler: logging.FileHandler | None = None


def temp_log_in_config(config_file: str) -> bool:
    """STATION.enable_log_to_temp of a config file, read before the config
    manager exists (logging starts first); False if it cannot be read."""
    parser = configparser.ConfigParser(inline_comment_prefixes="#", allow_no_value=True)
    try:
        parser.read(config_file)
        return parser.getboolean("STATION", "enable_log_to_temp", fallback=False)
    except (configparser.Error, ValueError, OSError):
        return False


def setup_logging(filename: str = "", level: str = "DEBUG", temp_filename: str = ""):
    """
    Configure stdlib logging + structlog.
    - Console: colored (always)
    - File handler: RotatingFileHandler (10 MB, 10 backups), if filename is given
    - Temp file handler: one plain file without control characters, if
      temp_filename is given (see temp_log_filename, set_temp_logging)
    """
    global _temp_handler
    _temp_handler = None  # dictConfig closes all handlers of an earlier setup
    timestamper = structlog.processors.TimeStamper(fmt="iso")
    pre_chain = [
        structlog.stdlib.add_log_level,
        timestamper,
    ]

    config_dict = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "plain": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processor": structlog.dev.ConsoleRenderer(colors=False),
                "foreign_pre_chain": pre_chain,
            },
            "colored": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processor": structlog.dev.ConsoleRenderer(colors=True),
                "foreign_pre_chain": pre_chain,
            },
        },
        "handlers": {
            "default": {
                "level": level,
                "class": "logging.StreamHandler",
                "formatter": "colored",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "": {
                "handlers": ["default"],
                "level": level,
                "propagate": True,
            },
        },
    }

    if filename:
        config_dict["handlers"]["file"] = {
            "level": level,
            "class": "logging.handlers.RotatingFileHandler",
            "filename": filename,
            "maxBytes": 10 * 1024 * 1024,  # 10 MB
            "backupCount": 10,
            "formatter": "plain",
        }
        config_dict["loggers"][""]["handlers"].append("file")

    logging.config.dictConfig(config_dict)

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            timestamper,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    logger = structlog.get_logger()

    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = handle_exception

    if filename:
        logger.info("Logger initialized", log_file=filename)
    if temp_filename:
        set_temp_logging(True, temp_filename)

    return logger


def temp_log_path() -> str:
    """Path of the temp-directory log file being written, "" if none."""
    return _temp_handler.baseFilename if _temp_handler else ""


def set_temp_logging(enabled: bool, filename: str = "") -> str:
    """
    Start or stop logging into a file in the temp directory while the server
    runs (STATION.enable_log_to_temp, --log-to-temp, FREEDATA_LOG_TO_TEMP).
    Starting opens a new file, named by the time (temp_log_filename) unless
    filename is given; starting while a file is open keeps that one. Returns
    the path of the file now written, "" when off.
    """
    global _temp_handler
    root = logging.getLogger()
    logger = structlog.get_logger("Logging")
    if enabled and _temp_handler is None:
        handler = logging.FileHandler(filename or temp_log_filename(), encoding="utf-8")
        handler.setLevel(root.level)
        handler.setFormatter(
            StripControlCharsFormatter(
                processor=structlog.dev.ConsoleRenderer(colors=False),
                foreign_pre_chain=[structlog.stdlib.add_log_level, structlog.processors.TimeStamper(fmt="iso")],
            )
        )
        root.addHandler(handler)
        _temp_handler = handler
        logger.info("Logging also to temp file", temp_log_file=handler.baseFilename)
    elif not enabled and _temp_handler is not None:
        handler = _temp_handler
        logger.info("Logging to temp file stopped", temp_log_file=handler.baseFilename)
        root.removeHandler(handler)
        handler.close()
        _temp_handler = None
    return temp_log_path()
