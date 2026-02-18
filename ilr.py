#!/usr/bin/env python3

# pylint: disable=line-too-long,missing-module-docstring,missing-class-docstring,missing-function-docstring

# Copyright (c) 2026, Julian Müller (ChaoticByte)

from argparse import ArgumentParser
from os import environ, getuid
from pathlib import Path
from socket import socket, AF_UNIX, SOCK_STREAM
from struct import pack
from sys import stderr
from time import time, sleep

import numpy as np
from mss import mss
from PIL import Image
from yaml import safe_load as yaml_safe_load
from skimage.metrics import normalized_root_mse # pylint: disable=no-name-in-module


# Screenshot frequencies

FREQ_DETECT_DEFAULT = 30.0
FREQ_DUMP_DEFAULT = 1.0


# Methods for calculating the difference between reference image an current screenshot

METHOD_NRMSE = "nrmse"
METHODS = [
    METHOD_NRMSE
]

#

class Profile:

    def __init__(
        self,
        reference_image: Path,
        monitor: int,
        left: int, top: int,
        width: int, height: int,
        difference_method: str,
        difference_threshold: float,
        target_dps: float = 30,
        profile_yml_file: Path = None
    ):
        if not isinstance(reference_image, Path):
            raise TypeError("reference_image must be Path")
        self.reference_image = reference_image
        self.monitor = int(monitor)
        self.left = int(left)
        self.top = int(top)
        self.width = int(width)
        self.height = int(height)
        if not difference_method in METHODS:
            raise ValueError(f"Unknown method '{difference_method}' - supported values: {' '.join(METHODS)}")
        self.difference_method = difference_method
        self.diff_threshold = float(difference_threshold)
        self.profile_yml_file = profile_yml_file
        self.target_dps = float(target_dps)

    @classmethod
    def from_yml_file(cls, filepath: Path):
        profile_dict: dict = yaml_safe_load(filepath.read_text())
        return cls(
            Path(filepath.absolute().parent / Path(profile_dict["reference"])),
            profile_dict["monitor"],
            profile_dict["region"]["left"],
            profile_dict["region"]["top"],
            profile_dict["region"]["width"],
            profile_dict["region"]["height"],
            profile_dict["difference"]["method"],
            profile_dict["difference"]["threshold"],
            target_dps=profile_dict.get("target_dps", FREQ_DETECT_DEFAULT),
            profile_yml_file=filepath
        )

# Functions for the LibreSplit part

LIBRESPLIT_CMD_START_SPLIT = 0
LIBRESPLIT_CMD_STOP_RESET = 1

def get_xdg_runtime_dir() -> Path:
    if "XDG_RUNTIME_DIR" in environ:
        return Path(environ["XDG_RUNTIME_DIR"]).absolute()
    return Path(f"/run/user/{getuid}")

def get_libresplit_socket_file() -> Path:
    return get_xdg_runtime_dir() / "libresplit.sock"

def libresplit_ctl(cmd: int, address: Path):
    with socket(AF_UNIX, SOCK_STREAM) as sock:
        try:
            cmd_payload = pack("<I", cmd)
            sock.connect(str(address.absolute().resolve()))
            sock.sendall(pack(">I", len(cmd_payload)) + cmd_payload)
        except Exception as err:
            print(f"Couldn't send command to {str(address)}", file=stderr)
            print(err.with_traceback(None)) # ?


# Calculate difference -> match reference

def match_reference(reference, current, profile: Profile) -> tuple[bool, float]:
    if profile.difference_method == METHOD_NRMSE:
        diff = normalized_root_mse(reference, current)
        return diff < profile.diff_threshold, diff
    # elif ...
    raise RuntimeError(f"Unknown method {profile.difference_method}")


# image-related helper functions

def remove_alpha(img):
    return np.delete(img, 3, axis=2) # alpha has idx 3


def grab(mss_instance, monitor, profile: Profile):
    return mss_instance.grab({
                "left": monitor["left"] + profile.left,
                "top": monitor["top"] + profile.top,
                "width": profile.width,
                "height": profile.height
            })

def grab_array_noalpha(mss_instance, monitor, profile):
    return remove_alpha(np.array(grab(mss_instance, monitor, profile)))


# main functions

def run(profile: Profile, dump_diff_only: bool = False):
    ref = Image.open(profile.reference_image, formats=["png"])
    ref.load()
    ref = np.asarray(ref)[:, :, ::-1] # RGB to BGR
    state_loading = False
    libresplit_socket_file = get_libresplit_socket_file()
    with mss() as ms:
        mon = ms.monitors[profile.monitor]
        t1 = time()
        while True:
            current = grab_array_noalpha(ms, mon, profile)
            is_match, diff = match_reference(ref, current, profile)
            if dump_diff_only:
                print(diff)
            else:
                if is_match:
                    if not state_loading:
                        state_loading = True
                        print("loading")
                        libresplit_ctl(LIBRESPLIT_CMD_STOP_RESET, libresplit_socket_file)
                else:
                    if state_loading:
                        state_loading = False
                        print("not loading")
                        libresplit_ctl(LIBRESPLIT_CMD_START_SPLIT, libresplit_socket_file)
            dt = time() - t1
            sleep(max(0, (1.0 / profile.target_dps) - dt))
            t1 = time()


def dumpimgs(profile: Profile, dump_frequency: float = FREQ_DUMP_DEFAULT):
    print(f"Dumping {dump_frequency} image(s) per second...")
    if profile.profile_yml_file is None:
        out_dir = Path(f"dump_{int(time())}")
    else:
        out_dir = profile.profile_yml_file.absolute().parent / f"dump_{int(time())}"
    out_dir.mkdir()
    with mss() as ms:
        mon = ms.monitors[profile.monitor]
        while True:
            sx = grab(ms, mon, profile)
            img = Image.frombytes("RGB", sx.size, sx.bgra, "raw", "BGRX")
            img.save(out_dir / f"{int(time() * 10)}.png", format="png")
            # we don't need to use dt here, because the added precision is negligible here
            sleep(1.0 / dump_frequency)


# entrypoint

CMD_RUN = "run"
CMD_DUMPDIFF = "dump-difference"
CMD_DUMPIMAGES = "dump-images"

if __name__ == "__main__":
    argparser = ArgumentParser()
    argparser.add_argument("command", type=str, choices=[CMD_RUN, CMD_DUMPDIFF, CMD_DUMPIMAGES], help="(str) command")
    argparser.add_argument("profile", type=Path, help="(str) Path to the profile configuration")
    argparser.add_argument("--dump-freq", default=FREQ_DUMP_DEFAULT, type=float, help=f"(float) How many images to save per second (command: {CMD_DUMPIMAGES}, optional, default: {FREQ_DUMP_DEFAULT})")
    args = argparser.parse_args()
    # parse profile
    p = Profile.from_yml_file(args.profile)
    # command
    arg_cmd = args.command
    if arg_cmd == CMD_RUN:
        run(p)
    elif arg_cmd == CMD_DUMPDIFF:
        run(p, dump_diff_only=True)
    elif arg_cmd == CMD_DUMPIMAGES:
        dumpimgs(p, args.dump_freq)
