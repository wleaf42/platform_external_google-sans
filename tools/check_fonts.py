#!/usr/bin/env python3
"""Check the shipped fonts against the Pixel reference, the overrides and the inputs.

Usage:
    python3 -I tools/check_fonts.py --fonttools <tree>/external/fonttools/Lib \
        --pyyaml <tree>/external/python/pyyaml/lib \
        --input <google/fonts checkout with the pinned inputs> [--module <this repository>]

For each shipped font and its pinned input, checks that
  - a fresh run of generate_fonts.py reproduces the shipped files byte for byte
  - the table set is the reference's and tables without glyph references are
    byte-identical
  - head, hhea, maxp and OS/2 change only in the copied and recalculated fields, which
    have the reference and recalculated values, and the line metrics are the reference's
  - the cmap is the reference's minus config/overrides.yml, and only the listed
    codepoints of the reference are missing upstream
  - kept glyphs keep their name, outline, bounds, instructions, metrics and gvar data
  - outlines and advances match at several axis locations
  - GSUB and GPOS have the reference scripts and features, with unchanged single,
    multiple, alternate and ligature substitutions and unchanged kerning at the default
    location
  - no glyph of a removed codepoint is left

The expectations are restated from the config files here, so a bug in generate_fonts.py
is not repeated. Not compared: contextual lookups, mark positioning, GDEF and kerning
variations.
"""

import argparse
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

# None of these reference glyph identifiers, so subsetting must leave them
# byte-identical.
IDENTICAL_TABLES = [
    "fvar",
    "avar",
    "STAT",
    "name",
    "gasp",
    "prep",
    "cvt ",
    "fpgm",
    "MVAR",
]

# Fields generate_fonts.py copies from the reference, as (table, field, section, key).
COPIED_FIELDS = [
    ("OS/2", "usWinAscent", "os2", "usWinAscent"),
    ("OS/2", "usWinDescent", "os2", "usWinDescent"),
    ("head", "yMin", "head", "yMin"),
    ("head", "yMax", "head", "yMax"),
]

# Recomputed by the subsetter or on save. indexToLocFormat changes because glyf is
# smaller.
SUBSET_CHANGES = {
    "head": {"checkSumAdjustment", "indexToLocFormat"},
    "maxp": {"numGlyphs"},
    "hhea": {"numberOfHMetrics"},
}

# Some of the locations fonts_customization.xml uses, plus the axis extremes, where
# variation errors would show most.
GOOGLE_SANS_LOCATIONS = [
    {"wght": 400, "opsz": 17, "GRAD": 0},
    {"wght": 500, "opsz": 18, "GRAD": 0},
    {"wght": 600, "opsz": 17, "GRAD": 0},
    {"wght": 700, "opsz": 18, "GRAD": 0},
    {"wght": 700, "opsz": 17, "GRAD": -50},
    {"wght": 400, "opsz": 18, "GRAD": 200},
]
LOCATIONS = {
    "GoogleSans-Regular.ttf": GOOGLE_SANS_LOCATIONS,
    "GoogleSans-Italic.ttf": GOOGLE_SANS_LOCATIONS,
    "GoogleSansFlex-Regular.ttf": [
        {"wght": 100, "opsz": 11, "wdth": 100, "ROND": 0, "slnt": 0},
        {"wght": 400, "opsz": 14, "wdth": 100, "ROND": 0, "slnt": -10},
        {"wght": 600, "opsz": 16, "wdth": 100, "ROND": 100, "slnt": 0},
        {"wght": 800, "opsz": 32, "wdth": 100, "ROND": 100, "slnt": 0},
        {"wght": 1000, "opsz": 57, "wdth": 100, "ROND": 100, "slnt": 0},
        {"wght": 1, "opsz": 1, "wdth": 25, "ROND": 0, "slnt": -10},
        {"wght": 1000, "opsz": 144, "wdth": 151, "ROND": 100, "slnt": 0},
    ],
}

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print(f"  FAIL {message}")

    return condition


def plain(value):
    # Nested records such as OS/2 panose have no equality, so compare their fields.
    return vars(value) if hasattr(value, "__dict__") else value


def table_fields(table):
    return {
        field: plain(value)
        for field, value in vars(table).items()
        if not field.startswith("_") and field != "tableTag"
    }


def compare_fields(tag, source, output, allowed):
    source_fields = table_fields(source[tag])
    output_fields = table_fields(output[tag])

    changed = sorted(
        field
        for field in set(source_fields) | set(output_fields)
        if source_fields.get(field) != output_fields.get(field)
    )
    unexpected = [field for field in changed if field not in allowed]
    check(not unexpected, f"{tag}: unexpected field changes {unexpected}")

    return [
        (field, source_fields.get(field), output_fields.get(field)) for field in changed
    ]


def outline_bounds(font):
    """Return the bounding box of all glyph outlines, from the coordinates."""
    from fontTools.misc.roundTools import otRound

    glyph_table = font["glyf"]
    x_values, y_values = [], []

    for name in font.getGlyphOrder():
        if glyph_table[name].numberOfContours == 0:
            continue

        coordinates, _, _ = glyph_table[name].getCoordinates(glyph_table)
        x_values.extend(otRound(x) for x, _ in coordinates)
        y_values.extend(otRound(y) for _, y in coordinates)

    return {
        "xMin": min(x_values),
        "yMin": min(y_values),
        "xMax": max(x_values),
        "yMax": max(y_values),
    }


def expected_recalculation(name, font):
    table, _, field = name.rpartition(".")

    if table == "head":
        return outline_bounds(font)[field]

    raise KeyError(name)


def glyph_record(glyph_table, name):
    glyph = glyph_table[name]
    record = {"contours": glyph.numberOfContours}

    if glyph.numberOfContours == 0:
        return record

    record["bounds"] = (glyph.xMin, glyph.yMin, glyph.xMax, glyph.yMax)
    record["program"] = (
        glyph.program.getBytecode() if hasattr(glyph, "program") else b""
    )

    if glyph.isComposite():
        record["components"] = [
            (
                component.glyphName,
                component.flags,
                getattr(component, "x", None),
                getattr(component, "y", None),
                getattr(component, "transform", None),
                getattr(component, "firstPt", None),
                getattr(component, "secondPt", None),
            )
            for component in glyph.components
        ]
    else:
        record["coordinates"] = list(glyph.coordinates)
        record["contour_ends"] = list(glyph.endPtsOfContours)
        record["flags"] = list(glyph.flags)

    return record


def gvar_record(font, name):
    if "gvar" not in font:
        return None

    return [
        (tuple(sorted(variation.axes.items())), list(variation.coordinates))
        for variation in font["gvar"].variations.get(name, [])
    ]


def recorded(glyph_set, name):
    from fontTools.pens.recordingPen import RecordingPen

    # Use one glyph object: without HVAR, drawing sets its width from the gvar phantom
    # points, and a new lookup would return the default width.
    glyph = glyph_set[name]
    pen = RecordingPen()
    glyph.draw(pen)

    return pen.value, glyph.width


def same_glyph(source, output):
    # fontTools rounds an advance taken from the gvar phantom points with otRound but
    # keeps the fraction of one taken from HVAR, so they can differ by half a unit.
    return source[0] == output[0] and abs(source[1] - output[1]) <= 0.5


def script_feature_indices(font, tag, scripts):
    indices = set()

    for script_record in font[tag].table.ScriptList.ScriptRecord:
        if script_record.ScriptTag not in scripts:
            continue

        script = script_record.Script
        language_systems = [script.DefaultLangSys] + [
            language_record.LangSys for language_record in script.LangSysRecord
        ]

        for language_system in language_systems:
            if language_system is None:
                continue

            indices.update(language_system.FeatureIndex)

            if language_system.ReqFeatureIndex != 0xFFFF:
                indices.add(language_system.ReqFeatureIndex)

    return indices


def feature_lookups(font, tag, scripts=None):
    """Return the lookups of each feature, only those of scripts if given."""
    lookups_by_feature = {}

    if tag not in font or not font[tag].table.FeatureList:
        return lookups_by_feature

    indices = None if scripts is None else script_feature_indices(font, tag, scripts)

    for index, feature_record in enumerate(font[tag].table.FeatureList.FeatureRecord):
        if indices is not None and index not in indices:
            continue

        lookups_by_feature.setdefault(feature_record.FeatureTag, set()).update(
            feature_record.Feature.LookupListIndex
        )

    return lookups_by_feature


def script_tags(font, tag):
    if tag not in font or not font[tag].table.ScriptList:
        return set()

    return {
        script_record.ScriptTag
        for script_record in font[tag].table.ScriptList.ScriptRecord
    }


def subtables(lookup):
    for subtable in lookup.SubTable:
        if lookup.LookupType in (7, 9) and hasattr(subtable, "ExtSubTable"):
            yield subtable.ExtSubTable, subtable.ExtSubTable.LookupType
        else:
            yield subtable, lookup.LookupType


def flatten_gsub(font, feature, kept_glyphs, scripts):
    rules = set()
    lookups = font["GSUB"].table.LookupList.Lookup

    for lookup_index in feature_lookups(font, "GSUB", scripts).get(feature, ()):
        for subtable, kind in subtables(lookups[lookup_index]):
            if kind == 1:
                rules |= {
                    ("single", source_glyph, output_glyph)
                    for source_glyph, output_glyph in subtable.mapping.items()
                    if source_glyph in kept_glyphs and output_glyph in kept_glyphs
                }
            elif kind == 2:
                rules |= {
                    ("multiple", source_glyph, tuple(output_glyphs))
                    for source_glyph, output_glyphs in subtable.mapping.items()
                    if source_glyph in kept_glyphs and set(output_glyphs) <= kept_glyphs
                }
            elif kind == 3:
                rules |= {
                    ("alternate", source_glyph, tuple(alternates))
                    for source_glyph, alternates in subtable.alternates.items()
                    if source_glyph in kept_glyphs
                }
            elif kind == 4:
                for first_glyph, ligatures in subtable.ligatures.items():
                    for ligature in ligatures:
                        sequence = (first_glyph, *ligature.Component)

                        if (
                            set(sequence) <= kept_glyphs
                            and ligature.LigGlyph in kept_glyphs
                        ):
                            rules.add(("ligature", sequence, ligature.LigGlyph))

    # Lookup indices change in the subset, so leave them out.
    return rules


def flatten_kern(font, kept_glyphs, scripts):
    pairs = {}

    if "GPOS" not in font:
        return pairs

    lookups = font["GPOS"].table.LookupList.Lookup

    for lookup_index in sorted(feature_lookups(font, "GPOS", scripts).get("kern", ())):
        for subtable, kind in subtables(lookups[lookup_index]):
            if kind != 2:
                continue

            if subtable.Format == 1:
                for first_glyph, pair_set in zip(
                    subtable.Coverage.glyphs, subtable.PairSet
                ):
                    if first_glyph not in kept_glyphs:
                        continue

                    for pair_value_record in pair_set.PairValueRecord:
                        second_glyph = pair_value_record.SecondGlyph

                        if (
                            second_glyph in kept_glyphs
                            and pair_value_record.Value1 is not None
                        ):
                            pairs.setdefault(
                                (first_glyph, second_glyph),
                                getattr(pair_value_record.Value1, "XAdvance", 0),
                            )
            else:
                first_classes = subtable.ClassDef1.classDefs
                second_classes = subtable.ClassDef2.classDefs
                glyphs_by_second_class = {}

                for glyph_name in kept_glyphs:
                    glyphs_by_second_class.setdefault(
                        second_classes.get(glyph_name, 0), []
                    ).append(glyph_name)

                covered_glyphs = [
                    glyph_name
                    for glyph_name in subtable.Coverage.glyphs
                    if glyph_name in kept_glyphs
                ]

                for first_glyph in covered_glyphs:
                    first_class_record = subtable.Class1Record[
                        first_classes.get(first_glyph, 0)
                    ]

                    for second_class, second_class_record in enumerate(
                        first_class_record.Class2Record
                    ):
                        x_advance = (
                            getattr(second_class_record.Value1, "XAdvance", 0)
                            if second_class_record.Value1 is not None
                            else 0
                        )

                        if not x_advance:
                            continue

                        for second_glyph in glyphs_by_second_class.get(
                            second_class, ()
                        ):
                            pairs.setdefault((first_glyph, second_glyph), x_advance)

    return {pair: value for pair, value in pairs.items() if value}


def check_tables(source, output, reference):
    expected_tables = sorted(set(source.keys()) & set(reference["tables"]))
    output_tables = sorted(set(output.keys()) - {"GlyphOrder"})
    check(
        output_tables == expected_tables,
        f"table set differs: {output_tables} vs expected {expected_tables}",
    )
    print(f"  removed tables: {sorted(set(source.keys()) - set(output.keys()))}")

    for tag in IDENTICAL_TABLES:
        if tag in source and tag in output:
            check(
                source.getTableData(tag) == output.getTableData(tag),
                f"{tag} is not byte-identical",
            )

    print(f"  identical tables: {[tag for tag in IDENTICAL_TABLES if tag in output]}")


def check_metrics(source, output, reference, font_overrides):
    allowed = {tag: set(fields) for tag, fields in SUBSET_CHANGES.items()}

    for table, field, _, _ in COPIED_FIELDS:
        allowed.setdefault(table, set()).add(field)

    for name in font_overrides["recalculate"]:
        table, _, field = name.rpartition(".")
        allowed.setdefault(table, set()).add(field)

    for tag in ("head", "OS/2", "maxp", "hhea"):
        differences = compare_fields(tag, source, output, allowed.get(tag, set()))

        for field, before, after in differences:
            print(f"  {tag}.{field}: {before} -> {after}")

    for table, field, section, key in COPIED_FIELDS:
        value, expected = getattr(output[table], field), reference[section][key]
        check(value == expected, f"{table}.{field} {value} != reference {expected}")

    for name in font_overrides["recalculate"]:
        table, _, field = name.rpartition(".")
        value = getattr(output[table], field)
        expected = expected_recalculation(name, output)
        check(value == expected, f"{name} {value} != recalculated {expected}")

    os2, hhea = output["OS/2"], output["hhea"]
    line_metrics = {
        "hhea_ascent": hhea.ascent,
        "hhea_descent": hhea.descent,
        "hhea_line_gap": hhea.lineGap,
        "typo_ascender": os2.sTypoAscender,
        "typo_descender": os2.sTypoDescender,
        "typo_line_gap": os2.sTypoLineGap,
        "use_typo_metrics": bool(os2.fsSelection & (1 << 7)),
    }
    check(
        line_metrics == reference["line_metrics"],
        f"line metrics {line_metrics} != reference {reference['line_metrics']}",
    )


def check_cmap(source, output, reference, overrides, font_overrides):
    source_character_map = source.getBestCmap()
    output_character_map = output.getBestCmap()
    reference_codepoints = common.parse_codepoints(reference["codepoints"])
    removed_codepoints = common.parse_codepoints(overrides["remove_codepoints"])

    missing = reference_codepoints - set(source_character_map) - removed_codepoints
    expected_missing = common.parse_codepoints(font_overrides["missing_upstream"])
    check(
        missing == expected_missing,
        f"reference codepoints missing upstream {common.format_codepoints(missing)} "
        f"!= config/overrides.yml {common.format_codepoints(expected_missing)}",
    )

    expected = (reference_codepoints & set(source_character_map)) - removed_codepoints
    extra = common.format_codepoints(set(output_character_map) - expected)[:20]
    absent = common.format_codepoints(expected - set(output_character_map))[:20]
    check(
        set(output_character_map) == expected,
        f"cmap differs from the reference: extra {extra} missing {absent}",
    )

    check(
        all(
            source_character_map.get(codepoint) == output_character_map[codepoint]
            for codepoint in output_character_map
        ),
        "a codepoint maps to a differently named glyph",
    )

    for subtable in output["cmap"].tables:
        check(
            set(subtable.cmap) <= set(output_character_map),
            f"cmap subtable {subtable.platformID}/{subtable.platEncID} has extra codepoints",
        )

    print(
        f"  cmap: {len(output_character_map)} codepoints"
        f" (input {len(source_character_map)}, reference {len(reference_codepoints)})"
    )


def check_glyphs(output_name, source, output, overrides):
    glyph_names = output.getGlyphOrder()
    kept_glyphs = set(glyph_names)

    unknown_glyphs = sorted(kept_glyphs - set(source.getGlyphOrder()))[:10]
    check(not unknown_glyphs, f"glyphs not in input: {unknown_glyphs}")

    removed_codepoints = common.parse_codepoints(overrides["remove_codepoints"])
    removed_glyphs = {
        glyph_name
        for codepoint, glyph_name in source.getBestCmap().items()
        if codepoint in removed_codepoints
    } - set(output.getBestCmap().values())
    left = sorted(kept_glyphs & removed_glyphs)
    check(not left, f"glyphs of removed codepoints left: {left}")

    glyf_mismatches = [
        name
        for name in glyph_names
        if glyph_record(source["glyf"], name) != glyph_record(output["glyf"], name)
    ]
    check(not glyf_mismatches, f"glyf differs for {glyf_mismatches[:10]}")

    hmtx_mismatches = [
        name for name in glyph_names if source["hmtx"][name] != output["hmtx"][name]
    ]
    check(not hmtx_mismatches, f"hmtx differs for {hmtx_mismatches[:10]}")

    gvar_mismatches = [
        name
        for name in glyph_names
        if gvar_record(source, name) != gvar_record(output, name)
    ]
    check(not gvar_mismatches, f"gvar differs for {gvar_mismatches[:10]}")

    identical = not (glyf_mismatches or hmtx_mismatches or gvar_mismatches)
    print(f"  {len(glyph_names)} glyphs: glyf, hmtx and gvar identical: {identical}")

    # A glyph set at a location applies avar, gvar and HVAR per glyph, which is much
    # faster than instancing whole fonts.
    for location in LOCATIONS[output_name]:
        source_glyphs = source.getGlyphSet(location=location)
        output_glyphs = output.getGlyphSet(location=location)

        mismatches = [
            name
            for name in glyph_names
            if not same_glyph(
                recorded(source_glyphs, name), recorded(output_glyphs, name)
            )
        ]
        check(
            not mismatches,
            f"outline or advance differs at {location} for {mismatches[:10]}",
        )
        print(f"  at {location}: {len(glyph_names)} glyphs match: {not mismatches}")


def check_layout(source, output, reference):
    scripts = set()

    for tag in ("GSUB", "GPOS"):
        key = tag.lower()

        source_features = set(feature_lookups(source, tag))
        output_features = set(feature_lookups(output, tag))
        expected_features = source_features & set(reference[f"{key}_features"])
        check(
            output_features == expected_features,
            f"{tag} features {sorted(output_features)} != expected {sorted(expected_features)}",
        )
        removed = sorted(source_features - output_features)
        print(f"  {tag} features: {sorted(output_features)} (removed {removed})")

        source_scripts = script_tags(source, tag)
        output_scripts = script_tags(output, tag)
        expected_scripts = source_scripts & set(reference[f"{key}_scripts"])
        check(
            output_scripts == expected_scripts,
            f"{tag} scripts {sorted(output_scripts)} != expected {sorted(expected_scripts)}",
        )
        print(f"  {tag} scripts: {sorted(output_scripts)}")

        scripts |= set(reference[f"{key}_scripts"])

    # Contextual and chaining rules (lookup types 5 and 6) are not compared, because
    # that needs a shaping engine. Rules only reachable through removed scripts are
    # expected to go, so the input is compared through the kept scripts only.
    kept_glyphs = set(output.getGlyphOrder())
    rule_count = 0
    same = True

    for feature in sorted(feature_lookups(output, "GSUB")):
        source_rules = flatten_gsub(source, feature, kept_glyphs, scripts)
        output_rules = flatten_gsub(output, feature, kept_glyphs, scripts)
        same &= check(
            source_rules == output_rules,
            f"GSUB {feature} substitutions differ: {len(source_rules ^ output_rules)} rules",
        )
        rule_count += len(output_rules)

    print(f"  {rule_count} GSUB substitution rules of kept features identical: {same}")

    source_kerning = flatten_kern(source, kept_glyphs, scripts)
    output_kerning = flatten_kern(output, kept_glyphs, scripts)
    kerning_differences = set(source_kerning.items()) ^ set(output_kerning.items())
    check(
        source_kerning == output_kerning,
        f"kern pairs differ: {len(kerning_differences)}",
    )
    print(
        f"  {len(output_kerning)} default kern pairs identical: {source_kerning == output_kerning}"
    )


def check_font(output_name, upstream_font, overrides, arguments):
    from fontTools.ttLib import TTFont

    source_path = os.path.join(arguments.input, upstream_font["source"])
    output_path = os.path.join(arguments.module, output_name)
    reference = common.load_yaml(common.reference_path(output_name))
    font_overrides = overrides["fonts"][output_name]

    print(f"== {output_name}")
    pixel = reference["source"]
    print(f"  reference: {pixel['file']} {pixel['version']} ({pixel['image']})")
    print(f"  reference sha256: {pixel['sha256']}")

    check(
        common.file_sha256(source_path) == upstream_font["sha256"],
        f"input sha256 mismatch for {upstream_font['source']}",
    )

    source = TTFont(source_path)
    output = TTFont(output_path)

    check_tables(source, output, reference)
    check_metrics(source, output, reference, font_overrides)
    check_cmap(source, output, reference, overrides, font_overrides)
    check_glyphs(output_name, source, output, overrides)
    check_layout(source, output, reference)


def check_fresh_run(arguments):
    # A fresh run must reproduce the shipped files byte for byte, which proves they come
    # from the pinned inputs and the committed config.
    with tempfile.TemporaryDirectory() as temporary_directory:
        subprocess.run(
            [
                sys.executable,
                "-I",
                os.path.join(common.TOOLS_DIRECTORY, "generate_fonts.py"),
                "--fonttools",
                arguments.fonttools,
                "--pyyaml",
                arguments.pyyaml,
                "--input",
                arguments.input,
                "--output",
                temporary_directory,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )

        generated = sorted(os.listdir(temporary_directory))

        for name in generated:
            shipped = os.path.join(arguments.module, name)
            same = os.path.exists(shipped) and common.file_sha256(
                shipped
            ) == common.file_sha256(os.path.join(temporary_directory, name))
            check(same, f"{name} differs from a fresh generate_fonts.py run")

        print(f"fresh run reproduces {len(generated)} files: {not failures}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    common.add_library_arguments(parser)
    parser.add_argument("--input", required=True)
    parser.add_argument("--module", default=common.MODULE_DIRECTORY)
    arguments = parser.parse_args()

    common.add_library_paths(arguments)

    check_fresh_run(arguments)

    upstream = common.load_yaml(common.UPSTREAM_PATH)
    overrides = common.load_yaml(common.OVERRIDES_PATH)

    for output_name, upstream_font in upstream["fonts"].items():
        check_font(output_name, upstream_font, overrides, arguments)

    print("RESULT: " + ("PASS" if not failures else f"FAIL ({len(failures)})"))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
