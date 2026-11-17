import argparse
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


def setup_logging(filename: str = "", level: str = "DEBUG", temp_filename: str = ""):
    """
    Configure stdlib logging + structlog.
    - Console: colored (always)
    - File handler: RotatingFileHandler (10 MB, 10 backups), if filename is given
    - Temp file handler: one plain file without control characters, if
      temp_filename is given (see temp_log_filename)
    """
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

    if temp_filename:
        config_dict["formatters"]["stripped"] = {
            "()": StripControlCharsFormatter,
            "processor": structlog.dev.ConsoleRenderer(colors=False),
            "foreign_pre_chain": pre_chain,
        }
        config_dict["handlers"]["temp_file"] = {
            "level": level,
            "class": "logging.FileHandler",
            "filename": temp_filename,
            "encoding": "utf-8",
            "formatter": "stripped",
        }
        config_dict["loggers"][""]["handlers"].append("temp_file")

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
        logger.info("Logging also to temp file", temp_log_file=temp_filename)

    return logger
