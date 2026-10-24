"""Paths, YAML and codepoint helpers shared by the tools."""

import hashlib
import os
import sys

TOOLS_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
MODULE_DIRECTORY = os.path.dirname(TOOLS_DIRECTORY)
CONFIG_DIRECTORY = os.path.join(MODULE_DIRECTORY, "config")
UPSTREAM_PATH = os.path.join(CONFIG_DIRECTORY, "upstream.yml")
OVERRIDES_PATH = os.path.join(CONFIG_DIRECTORY, "overrides.yml")
REFERENCE_DIRECTORY = os.path.join(CONFIG_DIRECTORY, "reference")


def add_library_arguments(parser):
    parser.add_argument(
        "--fonttools",
        required=True,
        help="fontTools Lib directory (external/fonttools/Lib)",
    )
    parser.add_argument(
        "--pyyaml",
        required=True,
        help="PyYAML lib directory (external/python/pyyaml/lib)",
    )


def add_library_paths(arguments):
    """Make fontTools and PyYAML from the tree importable, as the tools run with -I."""
    sys.path.insert(0, os.path.abspath(arguments.fonttools))
    sys.path.insert(0, os.path.abspath(arguments.pyyaml))


def file_sha256(path):
    with open(path, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()


def load_yaml(path):
    import yaml

    with open(path, encoding="utf-8") as file:
        return yaml.safe_load(file)


def reference_path(output):
    """Return the reference file of an output font, named after it."""
    return os.path.join(REFERENCE_DIRECTORY, os.path.splitext(output)[0] + ".yml")


def parse_codepoint(text):
    return int(text.removeprefix("U+"), 16)


def parse_codepoints(entries):
    """Return the codepoints of entries in Rust range syntax.

    "U+0020" is one codepoint, "U+0030..=U+0039" includes U+0039 and "U+0030..U+003A"
    excludes U+003A.
    """
    codepoints = set()

    for entry in entries:
        if "..=" in entry:
            first, last = entry.split("..=")
            codepoints.update(range(parse_codepoint(first), parse_codepoint(last) + 1))
        elif ".." in entry:
            first, end = entry.split("..")
            codepoints.update(range(parse_codepoint(first), parse_codepoint(end)))
        else:
            codepoints.add(parse_codepoint(entry))

    return codepoints


def format_codepoints(codepoints):
    """Return codepoints as the shortest list of inclusive parse_codepoints entries."""
    ranges = []

    for codepoint in sorted(codepoints):
        if ranges and codepoint == ranges[-1][1] + 1:
            ranges[-1][1] = codepoint
        else:
            ranges.append([codepoint, codepoint])

    return [
        f"U+{low:04X}" if low == high else f"U+{low:04X}..=U+{high:04X}"
        for low, high in ranges
    ]
