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
from numpy.typing import ArrayLike

from mss import mss
from mss.screenshot import ScreenShot

from PIL import Image
from yaml import safe_load as yaml_safe_load
from skimage.metrics import normalized_root_mse # pylint: disable=no-name-in-module


# Screenshot frequencies

FREQ_DETECT_DEFAULT = 30.0
FREQ_DUMP_DEFAULT = 1.0

# Filters

FILTER_MEAN_GREYSCALE = "mean_greyscale"
FILTERS = [
    FILTER_MEAN_GREYSCALE
]


# Methods for calculating the difference between reference image an current screenshot

METHOD_NRMSE = "nrmse"
METHODS = [
    METHOD_NRMSE
]

#


class ProfileReferenceImage:

    def __init__(self, reference_fp: Path, mask_fp: Path = None):
        if not isinstance(reference_fp, Path):
            raise TypeError("reference must be Path")
        self.reference_fp = reference_fp
        self.mask_fp = None
        if mask_fp is not None:
            if not isinstance(mask_fp, Path):
                raise TypeError("mask_image must be either Path or None")
            self.mask_fp = mask_fp
        self.use_mask: bool = self.mask_fp is not None
        # for later
        self.reference = None
        self.mask = None

    def load(self, filters: list):
        ref = Image.open(self.reference_fp, formats=["png"])
        ref.load()
        ref = np.asarray(ref)
        ref = remove_alpha(ref)
        ref = ref[:, :, ::-1] # RGB to BGR
        ref = apply_filters(ref, filters)
        if self.use_mask:
            mask = Image.open(self.mask_fp, formats=["png"])
            mask.load()
            mask = np.asarray(mask)
            mask = remove_alpha(mask)
            mask = mask[:, :, ::-1] # RGB to BGR
            mask = apply_filters(mask, filters)
            ref = mask_img(ref, mask)
            self.mask = mask
        else:
            self.mask = None
        self.reference = ref


class Profile:

    def __init__(
        self,
        references: list[ProfileReferenceImage],
        monitor: int,
        left: int, top: int,
        width: int, height: int,
        difference_method: str,
        difference_threshold: float,
        target_dps: float = 30,
        filters: list[str] = [],
        profile_yml_file: Path = None
    ):
        self.references = []
        for r in references:
            if not isinstance(r, ProfileReferenceImage):
                raise ValueError("all elements of references must be of type ReferenceImage")
            self.references.append(r)
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
        self.filters = []
        for f in filters:
            if not f in FILTERS:
                raise ValueError(f"Unknown filter '{f}' - supported values: {' '.join(FILTERS)}")
            self.filters.append(f)

    @classmethod
    def from_yml_file(cls, filepath: Path):
        profile_dict: dict = yaml_safe_load(filepath.read_text())
        references = []
        for r in profile_dict["references"]:
            img = Path(filepath.absolute().parent / Path(r["image"]))
            mask = r.get("mask", None)
            if mask is not None:
                mask = filepath.absolute().parent / Path(mask)
            references.append(ProfileReferenceImage(img, mask))
        return cls(
            references,
            profile_dict["monitor"],
            profile_dict["region"]["left"],
            profile_dict["region"]["top"],
            profile_dict["region"]["width"],
            profile_dict["region"]["height"],
            profile_dict["difference"]["method"],
            profile_dict["difference"]["threshold"],
            target_dps=profile_dict.get("target_dps", FREQ_DETECT_DEFAULT),
            filters=profile_dict.get("filters", []),
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


# Image manipulation filters

def apply_filters(img: ArrayLike, filters: list[str]) -> ArrayLike:
    if FILTER_MEAN_GREYSCALE in filters:
        # changes array shape!
        img = np.mean(img, 2)
    return img

def mask_img(img: ArrayLike, mask: ArrayLike) -> ArrayLike:
    return img * mask

def remove_alpha(img: ArrayLike) -> ArrayLike:
    shape = img.shape
    if len(shape) == 3:
        if shape[2] == 4:
            return np.delete(img, 3, axis=2) # alpha has idx 3
        elif shape[2] == 3:
            return img
    raise RuntimeError("Invalid array shape, could not remove alpha")


# Calculate difference -> match reference

def match_reference(reference, current, profile: Profile) -> tuple[bool, float]:
    if profile.difference_method == METHOD_NRMSE:
        diff = normalized_root_mse(reference, current)
        return diff < profile.diff_threshold, diff
    # elif ...
    raise RuntimeError(f"Unknown method {profile.difference_method}")


# Grab a screenshot

def grab(mss_instance, monitor, profile: Profile) -> ScreenShot:
    return mss_instance.grab({
                "left": monitor["left"] + profile.left,
                "top": monitor["top"] + profile.top,
                "width": profile.width,
                "height": profile.height
            })

def grab_array_noalpha(mss_instance, monitor, profile) -> ArrayLike:
    return remove_alpha(np.array(grab(mss_instance, monitor, profile)))


# main functions

def run(profile: Profile, dump_diff_only: bool = False):
    for r in profile.references:
        r.load(profile.filters)
    #
    state_loading = False
    libresplit_socket_file = get_libresplit_socket_file()
    with mss() as ms:
        mon = ms.monitors[profile.monitor]
        t1 = time()
        while True:
            current = grab_array_noalpha(ms, mon, profile)
            current = apply_filters(current, profile.filters)
            had_match = False
            for r in profile.references:
                current_ = current.copy()
                if r.use_mask:
                    current_ = mask_img(current_, r.mask)
                is_match, diff = match_reference(r.reference, current_, profile)
                if dump_diff_only:
                    print(diff)
                else:
                    if is_match:
                        had_match = True
                        break
            if had_match:
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
