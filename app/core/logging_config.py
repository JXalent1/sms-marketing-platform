"""Application logging: a rotating daily file plus console output.

Campaign sends log one line per message. At 6,000 recipients that is a lot of
lines, and they are the only forensic record you have when a client asks why a
blast underperformed — keep them.
"""

import logging
import os
from datetime import datetime


def setup_logging(level: int = logging.INFO):
    os.makedirs("logs", exist_ok=True)

    file_handler = logging.FileHandler(f"logs/app_{datetime.now().strftime('%Y-%m-%d')}.log")
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(levelname)-8s | %(message)s"))
    console_handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    if not root.handlers:                      # avoid duplicate handlers on reload
        root.addHandler(file_handler)
        root.addHandler(console_handler)
    return root
