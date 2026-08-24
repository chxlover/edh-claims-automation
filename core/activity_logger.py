"""
===========================================================
EDH Claims Automation System
Module : Activity Logger
Version: 2.0
Author : EDH Development Team

Description:
    Central logging module for the entire Claims Automation
    System.

Features:
    ✔ Console Logging
    ✔ SQLite Logging
    ✔ Log Levels
    ✔ Thread Safe
    ✔ Timestamped
===========================================================
"""

from datetime import datetime
from threading import Lock
from pathlib import Path

from core.claims_database import db


class ActivityLogger:

    _lock = Lock()

    def __init__(self):
        self.console_output = True

    def _write(self, level: str, message: str):

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        log_line = f"[{timestamp}] [{level}] {message}"

        with self._lock:

            # Console
            if self.console_output:
                print(log_line)

            # SQLite
            try:
                db.log(level, message)
            except Exception as e:
                print(f"[LOGGER ERROR] {e}")

    # -------------------------------------------------
    # Public Methods
    # -------------------------------------------------

    def info(self, message):
        self._write("INFO", message)

    def success(self, message):
        self._write("SUCCESS", message)

    def warning(self, message):
        self._write("WARNING", message)

    def error(self, message):
        self._write("ERROR", message)

    def debug(self, message):
        self._write("DEBUG", message)


# Singleton
logger = ActivityLogger()


# -------------------------------------------------
# TEST
# -------------------------------------------------

if __name__ == "__main__":

    print("=" * 60)
    print("Activity Logger Test")
    print("=" * 60)

    logger.info("Claims Processor Started")

    logger.success("Hospital Number Found")

    logger.warning("Multiple Admissions Found")

    logger.error("Hospital Number Missing")

    logger.debug("OCR Confidence = 82")

    print("=" * 60)
    print("Logger Test Finished")
    print("=" * 60)