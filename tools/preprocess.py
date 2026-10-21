#!/usr/bin/env python3
"""Build the shipped fonts from the pinned google/fonts inputs.

Usage:
    python3 -I tools/preprocess.py --fonttools <tree>/external/fonttools/Lib \
        --input <google/fonts checkout with the pinned inputs> --output <directory>

README.md lists every modification and verify_preprocess.py checks them.
"""

import argparse
import hashlib
import os
import sys

# Scripts kept in Google Sans, as in the Pixel build, so other scripts fall back to the
# same system fonts as on Pixel. A codepoint is kept if its Script_Extensions intersect
# this set, so Common characters used only by other scripts, such as ARABIC COMMA, fall
# back too.
GOOGLE_SANS_SCRIPTS = {"Latn", "Grek", "Cyrl", "Hebr", "Zyyy", "Zinh"}

# Never shipped. U+E000..U+E006 are Google logos, and TRADEMARKS.md in google/fonts
# forbids uses that suggest affiliation with Google. remove_logo_ligatures also drops
# the default-on ligatures that produce them from text such as "Google_logo".
PRIVATE_USE_RANGES = [(0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD)]

# Kept by GOOGLE_SANS_SCRIPTS but missing from the Pixel build. Excluded so they fall
# back to the same fonts as on Pixel.
GOOGLE_SANS_EXCLUDE = {
    # They enclose the Arabic digits after them, which come from another font. Keeping
    # them would split mark and digits across fonts and break the enclosure.
    0x0605: "ARABIC NUMBER MARK ABOVE",
    0x06DD: "ARABIC END OF AYAH",
    0x08E2: "ARABIC DISPUTED END OF AYAH",
    # Excluded to match the Pixel cmap. Minikin and HarfBuzz handle these without font
    # support, so removing them is invisible.
    0x034F: "COMBINING GRAPHEME JOINER",
    0x200C: "ZERO WIDTH NON-JOINER",
    0x200D: "ZERO WIDTH JOINER",
    0x200E: "LEFT-TO-RIGHT MARK",
    0x200F: "RIGHT-TO-LEFT MARK",
    0x202A: "LEFT-TO-RIGHT EMBEDDING",
    0x202B: "RIGHT-TO-LEFT EMBEDDING",
    0x202C: "POP DIRECTIONAL FORMATTING",
    0x202D: "LEFT-TO-RIGHT OVERRIDE",
    0x202E: "RIGHT-TO-LEFT OVERRIDE",
    0x2066: "LEFT-TO-RIGHT ISOLATE",
    0x2067: "RIGHT-TO-LEFT ISOLATE",
    0x2068: "FIRST STRONG ISOLATE",
    0x2069: "POP DIRECTIONAL ISOLATE",
    0xFEFF: "ZERO WIDTH NO-BREAK SPACE",
    # Excluded to match the Pixel cmap. Without U+2010, minikin hyphenates with U+002D,
    # as on Pixel.
    0x2010: "HYPHEN",
    0x2011: "NON-BREAKING HYPHEN",
    # Missing from the Pixel build. Excluded so they fall back as on Pixel.
    0x01CE: "LATIN SMALL LETTER A WITH CARON",
    0x02BB: "MODIFIER LETTER TURNED COMMA",
    0x0951: "DEVANAGARI STRESS SIGN UDATTA",
    0x0952: "DEVANAGARI STRESS SIGN ANUDATTA",
    0x0953: "DEVANAGARI GRAVE ACCENT",
    0x0954: "DEVANAGARI ACUTE ACCENT",
    0x10FB: "GEORGIAN PARAGRAPH SEPARATOR",
    0x1E44: "LATIN CAPITAL LETTER N WITH DOT ABOVE",
    0x1E45: "LATIN SMALL LETTER N WITH DOT ABOVE",
    0x204F: "REVERSED SEMICOLON",
    0x20AD: "KIP SIGN",
    0x20BE: "LARI SIGN",
    0x22EF: "MIDLINE HORIZONTAL ELLIPSIS",
    0x2393: "DIRECT CURRENT SYMBOL FORM TWO",
    0x2E41: "REVERSED COMMA",
}

# Missing from the Pixel build of Google Sans Flex. Excluded so they fall back as on
# Pixel.
GOOGLE_SANS_FLEX_EXCLUDE = {
    0x0329: "COMBINING VERTICAL LINE BELOW",
    0x21E6: "LEFTWARDS WHITE ARROW",
    0x21E7: "UPWARDS WHITE ARROW",
    0x21E8: "RIGHTWARDS WHITE ARROW",
    0x21E9: "DOWNWARDS WHITE ARROW",
    0x2318: "PLACE OF INTEREST SIGN",
}

# Features of the Pixel build, kept so text shapes as on Pixel. The others add
# default-on behavior the Pixel build lacks (clig, rclt, rlig) or shape removed scripts.
GOOGLE_SANS_GSUB_FEATURES = {
    "c2sc",
    "calt",
    "case",
    "ccmp",
    "dlig",
    "dnom",
    "frac",
    "jalt",
    "liga",
    "lnum",
    "locl",
    "numr",
    "ordn",
    "pnum",
    "sinf",
    "smcp",
    "ss01",
    "ss02",
    "ss03",
    "ss04",
    "ss05",
    "ss06",
    "ss07",
    "ss08",
    "ss09",
    "subs",
    "sups",
    "tnum",
}
GOOGLE_SANS_GPOS_FEATURES = {"kern", "locl", "mark", "mkmk"}

# Upstream sizes these (1509/1079) for the removed scripts. The Pixel values fit the
# kept glyphs (yMax 1056, yMin -381), so all vertical metrics match Pixel. Google Sans
# Flex already has the Pixel values.
GOOGLE_SANS_WINDOWS_ASCENT = 1057
GOOGLE_SANS_WINDOWS_DESCENT = 382

# Skia uses head yMin/yMax as Paint.FontMetrics top/bottom, which TextView uses for font
# padding. Upstream has 2210 (U+1EB2). Pixel has 2007 (U+00C5) although its glyphs also
# reach 2210. Using 2007 makes padded line heights match Pixel. AOSP pins sans-serif the
# same way (check_vertical_metrics in fontchain_linter.py).
GOOGLE_SANS_FLEX_HEAD_Y_MAXIMUM = 2007

FONTS = [
    {
        "source": "ofl/googlesans/GoogleSans[GRAD,opsz,wght].ttf",
        "sha256": "d0a87d835a944b8b40d0e82a5651bb59ab97b936a2aeed5946eb57e7b2a3a90a",
        "output": "GoogleSans-Regular.ttf",
        "family": "google-sans",
        "gsub_features": GOOGLE_SANS_GSUB_FEATURES,
        "gpos_features": GOOGLE_SANS_GPOS_FEATURES,
    },
    {
        "source": "ofl/googlesans/GoogleSans-Italic[GRAD,opsz,wght].ttf",
        "sha256": "b6662abd2131cad1b248750149a7f2b644f871ebcc9af7fe4cd1135cd290b6d1",
        "output": "GoogleSans-Italic.ttf",
        "family": "google-sans",
        # Upstream Italic has no ss03.
        "gsub_features": GOOGLE_SANS_GSUB_FEATURES - {"ss03"},
        "gpos_features": GOOGLE_SANS_GPOS_FEATURES,
    },
    {
        "source": "ofl/googlesansflex/GoogleSansFlex[ROND,opsz,slnt,wdth,wght].ttf",
        "sha256": "a9ac770798857d5e8ac593ced42b22e40a7c08cec98319f92b0eccc2783a77ba",
        "output": "GoogleSansFlex-Regular.ttf",
        "family": "google-sans-flex",
        # Already matches the Pixel build, so no feature is removed.
        "gsub_features": None,
        "gpos_features": None,
    },
]

# Copied unchanged because the OFL requires its text to ship with the fonts.
LICENSE_FILES = [
    ("ofl/googlesans/OFL.txt", "OFL-GoogleSans.txt"),
    ("ofl/googlesansflex/OFL.txt", "OFL-GoogleSansFlex.txt"),
]


def file_sha256(path):
    with open(path, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()


def is_private_use(codepoint):
    return any(low <= codepoint <= high for low, high in PRIVATE_USE_RANGES)


def kept_codepoints(font, family):
    """Return the kept codepoints and the exclusions found in this font."""
    from fontTools import unicodedata

    upstream_codepoints = set(font.getBestCmap())
    if family == "google-sans":
        kept = {
            codepoint
            for codepoint in upstream_codepoints
            if set(unicodedata.script_extension(chr(codepoint))) & GOOGLE_SANS_SCRIPTS
        }
        exclusions = GOOGLE_SANS_EXCLUDE
    else:
        # Its coverage already matches the Pixel build apart from the exclusions.
        kept = set(upstream_codepoints)
        exclusions = GOOGLE_SANS_FLEX_EXCLUDE
    applied_exclusions = set(exclusions) & upstream_codepoints
    kept = {
        codepoint
        for codepoint in kept
        if not is_private_use(codepoint) and codepoint not in exclusions
    }
    return kept, applied_exclusions


def subsetter_options():
    from fontTools import subset

    options = subset.Options()
    # Keep all name records. They carry the copyright and license, and no renaming is
    # needed because neither license reserves a font name.
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.name_legacy = True
    # Keep glyph names so verify_preprocess.py can compare glyphs by name.
    options.glyph_names = True
    # Keep .notdef unchanged so missing characters look as upstream.
    options.notdef_glyph = True
    options.notdef_outline = True
    # Keep hinting so small sizes render as upstream.
    options.hinting = True
    # prune_features already removed the unwanted features, so keep everything left.
    options.layout_features = ["*"]
    options.layout_scripts = ["*"]
    # Keep every table so nothing changes beyond the listed modifications. The subsetter
    # cannot subset BASE, but BASE references no glyphs, so it passes through unchanged.
    options.drop_tables = []
    options.passthrough_tables = True
    # Do not recompute range bits, average width, context or timestamp, so they stay as
    # upstream and the output is reproducible.
    options.prune_unicode_ranges = False
    options.prune_codepage_ranges = False
    options.recalc_average_width = False
    options.recalc_max_context = False
    options.recalc_timestamp = False
    options.recalc_bounds = False
    return options


def prune_features(font, tag, keep):
    """Remove every feature of GSUB or GPOS whose tag is not in keep."""
    if keep is None or tag not in font:
        return
    table = font[tag]
    present = {
        feature_record.FeatureTag
        for feature_record in table.table.FeatureList.FeatureRecord
    }
    unknown = keep - present
    if unknown:
        sys.exit(f"{tag}: features to keep are not in the input: {sorted(unknown)}")
    # The subsetter prunes the lookups left unused.
    table.subset_feature_tags(keep)


def logo_glyphs(font):
    """Return the glyphs mapped from Private Use Area codepoints."""
    return {
        glyph_name
        for codepoint, glyph_name in font.getBestCmap().items()
        if is_private_use(codepoint)
    }


def remove_logo_ligatures(font, logos):
    """Remove the GSUB ligatures producing logo glyphs.

    Any other substitution producing them is an error, so an upstream update cannot bring
    them back unnoticed.
    """
    removed = 0
    for lookup in font["GSUB"].table.LookupList.Lookup:
        for subtable in lookup.SubTable:
            if lookup.LookupType == 7:
                subtable = subtable.ExtSubTable
            ligatures = getattr(subtable, "ligatures", None)
            if ligatures is not None:
                for first_glyph in list(ligatures):
                    kept = [
                        ligature
                        for ligature in ligatures[first_glyph]
                        if ligature.LigGlyph not in logos
                    ]
                    removed += len(ligatures[first_glyph]) - len(kept)
                    if kept:
                        ligatures[first_glyph] = kept
                    else:
                        del ligatures[first_glyph]
            for outputs in (getattr(subtable, "mapping", None) or {}).values():
                if set(outputs if isinstance(outputs, list) else [outputs]) & logos:
                    sys.exit("a non-ligature substitution produces a logo glyph")
            for alternates in (getattr(subtable, "alternates", None) or {}).values():
                if set(alternates) & logos:
                    sys.exit("an alternate substitution produces a logo glyph")
    return removed


def recalculate_head_bounds(font):
    """Set the head bounding box from the glyph outlines without modifying the glyphs."""
    from fontTools.misc.arrayTools import calcIntBounds

    glyph_table = font["glyf"]
    boxes = []
    for name in font.getGlyphOrder():
        glyph = glyph_table[name]
        if glyph.numberOfContours == 0:
            continue
        coordinates, _, _ = glyph.getCoordinates(glyph_table)
        if len(coordinates):
            boxes.append(calcIntBounds(coordinates))
    head = font["head"]
    head.xMin = min(box[0] for box in boxes)
    head.yMin = min(box[1] for box in boxes)
    head.xMax = max(box[2] for box in boxes)
    head.yMax = max(box[3] for box in boxes)


def process(font_definition, arguments):
    from fontTools import subset
    from fontTools.ttLib import TTFont

    source_path = os.path.join(arguments.input, font_definition["source"])
    digest = file_sha256(source_path)
    if digest != font_definition["sha256"]:
        sys.exit(
            f"{source_path}: sha256 {digest} does not match pinned {font_definition['sha256']}"
        )

    # Keep upstream bounds and timestamp unless set below, for a reproducible output.
    font = TTFont(source_path, recalcBBoxes=False, recalcTimestamp=False)

    codepoints, applied_exclusions = kept_codepoints(font, font_definition["family"])

    prune_features(font, "GSUB", font_definition["gsub_features"])
    prune_features(font, "GPOS", font_definition["gpos_features"])

    # Remove the ligatures first so the subsetter drops the logo glyphs.
    logos = logo_glyphs(font)
    removed = remove_logo_ligatures(font, logos)
    print(
        f"{font_definition['output']}: removed {removed} ligature rules producing {sorted(logos)}"
    )

    # Glyphs reachable through the remaining features are kept, so alternates, small
    # caps and ligatures keep working. Glyph identifiers are renumbered, and the glyph
    # data is not changed.
    subsetter = subset.Subsetter(options=subsetter_options())
    subsetter.populate(unicodes=sorted(codepoints))
    subsetter.subset(font)

    if font_definition["family"] == "google-sans":
        os2_table = font["OS/2"]
        os2_table.usWinAscent = GOOGLE_SANS_WINDOWS_ASCENT
        os2_table.usWinDescent = GOOGLE_SANS_WINDOWS_DESCENT
        # Upstream bounds (yMin -989, yMax 1461) cover the removed scripts and would
        # make padded text taller.
        recalculate_head_bounds(font)
    else:
        font["head"].yMax = GOOGLE_SANS_FLEX_HEAD_Y_MAXIMUM

    output_path = os.path.join(arguments.output, font_definition["output"])
    font.save(output_path)

    if arguments.print_codepoints:
        for codepoint in sorted(codepoints):
            print(f"{font_definition['output']} U+{codepoint:04X}")
    return output_path, applied_exclusions


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fonttools", required=True, help="fontTools Lib directory")
    parser.add_argument(
        "--input",
        required=True,
        help="google/fonts checkout with the pinned inputs (see README.md)",
    )
    parser.add_argument("--output", required=True, help="output directory")
    parser.add_argument(
        "--print-codepoints",
        action="store_true",
        help="print the codepoints kept in each font, one per line",
    )
    arguments = parser.parse_args()

    sys.path.insert(0, os.path.abspath(arguments.fonttools))
    import fontTools

    print(f"fontTools {fontTools.version}")
    os.makedirs(arguments.output, exist_ok=True)

    output_paths = []
    applied_exclusions = {"google-sans": set(), "google-sans-flex": set()}
    for font_definition in FONTS:
        output_path, applied = process(font_definition, arguments)
        output_paths.append(output_path)
        applied_exclusions[font_definition["family"]] |= applied

    # Fail on exclusions that match no input, so stale entries get noticed.
    for family, exclusions in (
        ("google-sans", GOOGLE_SANS_EXCLUDE),
        ("google-sans-flex", GOOGLE_SANS_FLEX_EXCLUDE),
    ):
        stale = set(exclusions) - applied_exclusions[family]
        if stale:
            stale_names = [f"U+{codepoint:04X}" for codepoint in sorted(stale)]
            sys.exit(f"{family}: exclusions not in any input: {stale_names}")

    for source, destination in LICENSE_FILES:
        with open(os.path.join(arguments.input, source), "rb") as file:
            data = file.read()
        with open(os.path.join(arguments.output, destination), "wb") as file:
            file.write(data)

    for output_path in output_paths:
        print(f"{file_sha256(output_path)}  {os.path.basename(output_path)}")


if __name__ == "__main__":
    main()
