import os
import asyncio
import argparse
import logging
from pathlib import Path
from enum import Enum

from .package import DFUPackage

class DFUMode(str, Enum):
    LEGACY      ="L"
    OPEN        ="O"
    SECURE      ="S"
    BUTTONLESS  ="B"

def get_log_name(path: Path=None):
    assert isinstance(path, Path)
    base_path = path
    idx = 0
    path = path.with_stem(f"{base_path.stem}-{idx}")
    while path.exists():
        idx += 1
        path = path.with_stem(f"{base_path.stem}-{idx}")
    return path

parser = argparse.ArgumentParser()
parser.add_argument("pkg_path", type=Path,
    help="path to the update package zip file")
parser.add_argument("--mode", type=DFUMode, default=DFUMode.SECURE,
    help="update type (L: legacy, O: open, S: secure, B: buttonless) (default: S)")
parser.add_argument("--log", type=Path, default=Path("dfu.log"),
    help="path to log file")

args = parser.parse_args()

log = logging.getLogger()
log_fmt = logging.Formatter("[ %(asctime)s | %(threadName)15s | %(levelname)6s ] %(message)s")
file_handler = logging.FileHandler(str(get_log_name(args.log)))
file_handler.setFormatter(log_fmt)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_fmt)
log.addHandler(file_handler)
log.addHandler(stream_handler)
log.setLevel(logging.DEBUG)

assert args.pkg_path.exists(), \
    "file not found: {}".format(str(pkg_path))

assert args.mode == DFUMode.SECURE, \
    "other dfu modes not supported"

log.info(f"starting dfu with pkg {str(args.pkg_path)}")

from .remote.secure import dfu

pkg = DFUPackage(args.pkg_path)

asyncio.run(dfu(pkg, log=log))