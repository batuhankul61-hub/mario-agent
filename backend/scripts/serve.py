"""Runs the Mario server without a console window (start-mario.ps1 starts it
with pythonw.exe). Logs go to backend/data/server.log, rotated at 1 MB."""

import logging.config
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
LOG = BACKEND / "data" / "server.log"

sys.path.insert(0, str(BACKEND))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG),
            "maxBytes": 1_000_000,
            "backupCount": 2,
            "encoding": "utf-8",
            "formatter": "plain",
        }
    },
    "root": {"handlers": ["file"], "level": "INFO"},
    "loggers": {
        name: {"handlers": [], "propagate": True}
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
    },
}


def main() -> None:
    import uvicorn  # noqa: PLC0415

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.config.dictConfig(LOGGING)
    try:
        uvicorn.run(
            "app.main:app", host="127.0.0.1", port=8100, log_config=LOGGING, app_dir=str(BACKEND)
        )
    except BaseException:
        logging.getLogger("serve").exception("Server stopped")
        raise


if __name__ == "__main__":
    main()
