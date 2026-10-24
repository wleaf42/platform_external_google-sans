#!/usr/bin/env python3
"""Generate the shipped fonts from the pinned google/fonts inputs and the reference.

Usage:
    python3 -I tools/generate_fonts.py --fonttools <tree>/external/fonttools/Lib \
        --pyyaml <tree>/external/python/pyyaml/lib \
        --input <google/fonts checkout with the pinned inputs> --output <directory>

config/upstream.yml pins the inputs, config/reference/*.yml describes the Pixel fonts
and config/overrides.yml lists the deliberate differences. tools/check_fonts.py checks
the result.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

# Fields copied from the reference, as (table, field, reference section, reference key).
#
# usWinAscent/usWinDescent: upstream sizes them for scripts the reference does not
# cover.
#
# head yMin/yMax: Skia uses them as Paint.FontMetrics top/bottom, which TextView uses
# for font padding, so the reference values make padded line heights match the Pixel
# fonts. The Pixel Google Sans Flex has 2007 (U+00C5) although its glyphs reach 2210
# (U+1EB2). AOSP pins sans-serif the same way (check_vertical_metrics in
# fontchain_linter.py).
COPIED_FIELDS = [
    ("OS/2", "usWinAscent", "os2", "usWinAscent"),
    ("OS/2", "usWinDescent", "os2", "usWinDescent"),
    ("head", "yMin", "head", "yMin"),
    ("head", "yMax", "head", "yMax"),
]


def subsetter_options(layout_scripts, drop_tables):
    from fontTools import subset

    options = subset.Options()

    # Keep all name records. They carry the copyright and license, and no renaming is
    # needed because neither license reserves a font name.
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.name_legacy = True

    # Keep glyph names so check_fonts.py can compare glyphs by name.
    options.glyph_names = True

    # Keep .notdef unchanged so missing characters look as upstream.
    options.notdef_glyph = True
    options.notdef_outline = True

    # Keep hinting so small sizes render as upstream.
    options.hinting = True

    # prune_features already removed the unwanted features, so keep everything left.
    # Removing scripts also drops the features and lookups only they reference.
    options.layout_features = ["*"]
    options.layout_scripts = layout_scripts

    # The reference has no BASE table. With the language_specific_extent flag, minikin
    # extends each line by the BASE extents of the run's script and locale, which are
    # larger than hhea upstream. Without BASE, line heights come from hhea alone and
    # match the Pixel fonts. The reference Google Sans Flex has no HVAR either, and its
    # advance variations are in gvar as upstream.
    options.drop_tables = drop_tables
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


def tags_in(font, tag, kind):
    if tag not in font:
        return set()

    table = font[tag].table

    if kind == "scripts":
        return {record.ScriptTag for record in table.ScriptList.ScriptRecord}

    return {record.FeatureTag for record in table.FeatureList.FeatureRecord}


def prune_features(output, font, tag, keep):
    """Remove every feature of GSUB or GPOS whose tag is not in keep."""
    present = tags_in(font, tag, "features")
    unknown = keep - present

    if unknown:
        sys.exit(
            f"{output}: reference {tag} features are not in the input: {sorted(unknown)}"
        )

    if keep != present:
        # The subsetter prunes the lookups left unused.
        font[tag].subset_feature_tags(keep)


def removed_glyphs(font, removed_codepoints):
    """Return the glyphs mapped only from removed codepoints."""
    character_map = font.getBestCmap()

    kept = {
        glyph
        for codepoint, glyph in character_map.items()
        if codepoint not in removed_codepoints
    }

    return {
        glyph
        for codepoint, glyph in character_map.items()
        if codepoint in removed_codepoints and glyph not in kept
    }


def remove_ligatures(font, glyphs):
    """Remove the GSUB ligatures producing glyphs.

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
                        if ligature.LigGlyph not in glyphs
                    ]
                    removed += len(ligatures[first_glyph]) - len(kept)

                    if kept:
                        ligatures[first_glyph] = kept
                    else:
                        del ligatures[first_glyph]

            for outputs in (getattr(subtable, "mapping", None) or {}).values():
                if set(outputs if isinstance(outputs, list) else [outputs]) & glyphs:
                    sys.exit("a non-ligature substitution produces a removed glyph")

            for alternates in (getattr(subtable, "alternates", None) or {}).values():
                if set(alternates) & glyphs:
                    sys.exit("an alternate substitution produces a removed glyph")

    return removed


def glyph_bounds(font):
    """Return the bounding box of the glyph outlines without modifying the glyphs."""
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

    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


# Fields config/overrides.yml can ask to compute from the kept glyphs.
RECALCULATIONS = {
    "head.xMin": lambda font: glyph_bounds(font)[0],
    "head.xMax": lambda font: glyph_bounds(font)[2],
}


def kept_codepoints(output, font, reference, overrides):
    """Return the reference codepoints the input has, minus the removed ones."""
    upstream_codepoints = set(font.getBestCmap())
    reference_codepoints = common.parse_codepoints(reference["codepoints"])
    removed_codepoints = common.parse_codepoints(overrides["remove_codepoints"])

    # Fail on unlisted differences, so a Pixel or upstream change gets noticed.
    missing = reference_codepoints - upstream_codepoints - removed_codepoints
    expected_missing = common.parse_codepoints(
        overrides["fonts"][output]["missing_upstream"]
    )

    if missing != expected_missing:
        sys.exit(
            f"{output}: reference codepoints missing upstream are "
            f"{common.format_codepoints(missing)}, config/overrides.yml lists "
            f"{common.format_codepoints(expected_missing)}"
        )

    return (reference_codepoints & upstream_codepoints) - removed_codepoints


def process(output, upstream_font, overrides, arguments):
    from fontTools import subset
    from fontTools.ttLib import TTFont

    source_path = os.path.join(arguments.input, upstream_font["source"])
    digest = common.file_sha256(source_path)

    if digest != upstream_font["sha256"]:
        sys.exit(
            f"{source_path}: sha256 {digest} does not match pinned {upstream_font['sha256']}"
        )

    reference = common.load_yaml(common.reference_path(output))

    # Keep upstream bounds and timestamp unless set below, for a reproducible output.
    font = TTFont(source_path, recalcBBoxes=False, recalcTimestamp=False)

    codepoints = kept_codepoints(output, font, reference, overrides)

    prune_features(output, font, "GSUB", set(reference["gsub_features"]))
    prune_features(output, font, "GPOS", set(reference["gpos_features"]))

    layout_scripts = set(reference["gsub_scripts"]) | set(reference["gpos_scripts"])
    input_scripts = tags_in(font, "GSUB", "scripts") | tags_in(font, "GPOS", "scripts")
    unknown = layout_scripts - input_scripts

    if unknown:
        sys.exit(f"{output}: reference scripts are not in the input: {sorted(unknown)}")

    tables = set(font.keys()) - {"GlyphOrder"}
    unknown = set(reference["tables"]) - tables

    if unknown:
        sys.exit(f"{output}: reference tables are not in the input: {sorted(unknown)}")

    drop_tables = sorted(tables - set(reference["tables"]))

    # Remove the ligatures first so the subsetter drops the glyphs.
    removed_codepoints = common.parse_codepoints(overrides["remove_codepoints"])
    glyphs = removed_glyphs(font, removed_codepoints)
    removed = remove_ligatures(font, glyphs)
    print(f"{output}: removed {removed} ligature rules producing {sorted(glyphs)}")

    # Glyphs reachable through the remaining features are kept, so alternates, small
    # caps and ligatures keep working. Glyph identifiers are renumbered, and the glyph
    # data is not changed.
    subsetter = subset.Subsetter(
        options=subsetter_options(sorted(layout_scripts), drop_tables)
    )
    subsetter.populate(unicodes=sorted(codepoints))
    subsetter.subset(font)

    for table, field, section, key in COPIED_FIELDS:
        setattr(font[table], field, reference[section][key])

    for name in overrides["fonts"][output]["recalculate"]:
        table, _, field = name.rpartition(".")
        setattr(font[table], field, RECALCULATIONS[name](font))

    output_path = os.path.join(arguments.output, output)
    font.save(output_path)

    if arguments.print_codepoints:
        for codepoint in sorted(codepoints):
            print(f"{output} U+{codepoint:04X}")

    return output_path


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    common.add_library_arguments(parser)
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

    common.add_library_paths(arguments)
    import fontTools

    print(f"fontTools {fontTools.version}")
    os.makedirs(arguments.output, exist_ok=True)

    upstream = common.load_yaml(common.UPSTREAM_PATH)
    overrides = common.load_yaml(common.OVERRIDES_PATH)

    output_paths = [
        process(output, upstream_font, overrides, arguments)
        for output, upstream_font in upstream["fonts"].items()
    ]

    for destination, source in upstream["licenses"].items():
        with open(os.path.join(arguments.input, source), "rb") as file:
            data = file.read()

        with open(os.path.join(arguments.output, destination), "wb") as file:
            file.write(data)

    for output_path in output_paths:
        print(f"{common.file_sha256(output_path)}  {os.path.basename(output_path)}")


if __name__ == "__main__":
    main()
