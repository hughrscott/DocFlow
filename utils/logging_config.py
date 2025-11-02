"""
Logging configuration for DocFlow.

Sets a consistent formatter and log level across the app, including uvicorn.
"""

import logging
import logging.config
import os
import json


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        return json.dumps(data)


def setup_logging(level: str = None) -> None:
    level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    fmt = os.getenv("LOG_FORMAT", "text").lower()

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            },
            "uvicorn": {
                "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "json" if fmt == "json" else "standard",
                "level": level,
            },
        },
        "loggers": {
            "": {"handlers": ["console"], "level": level},
            "uvicorn": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": ["console"], "level": level, "propagate": False},
        },
    }

    if fmt == "json":
        config["formatters"]["json"] = {"()": JsonFormatter}

    logging.config.dictConfig(config)
