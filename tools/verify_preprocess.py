#!/usr/bin/env python3
"""Check that preprocess.py changed only what README.md lists.

Usage:
    python3 -I tools/verify_preprocess.py --fonttools <tree>/external/fonttools/Lib \
        --input <google/fonts checkout with the pinned inputs> [--module <this repository>]

For each shipped font and its pinned input, checks that
  - a fresh run of preprocess.py reproduces the shipped files byte for byte
  - the table set is the same and tables without glyph references are byte-identical
  - head, hhea, maxp and OS/2 change only in the allowed fields
  - the cmap follows the codepoint rule
  - kept glyphs keep their name, outline, bounds, instructions, metrics and gvar data
  - outlines and advances match at several axis locations
  - GSUB and GPOS have the expected scripts and features, with unchanged single, multiple,
    alternate and ligature substitutions and unchanged kerning at the default location
  - no logo glyph is left

Not compared: contextual lookups, mark positioning, GDEF and kerning variations.
"""

import argparse
import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile

TOOLS_DIRECTORY = os.path.dirname(os.path.abspath(__file__))

# None of these reference glyph identifiers, so subsetting must leave them
# byte-identical.
IDENTICAL_TABLES = [
    "fvar",
    "avar",
    "STAT",
    "name",
    "BASE",
    "gasp",
    "prep",
    "cvt ",
    "fpgm",
    "MVAR",
]

# Fields preprocess.py changes on purpose. A change to any other field is a bug.
ALLOWED_CHANGES = {
    "google-sans": {
        "head": {"checkSumAdjustment", "xMin", "yMin", "xMax", "yMax"},
        "OS/2": {"usWinAscent", "usWinDescent"},
    },
    "google-sans-flex": {
        "head": {"checkSumAdjustment", "yMax"},
        "OS/2": set(),
    },
}
# Recomputed by the subsetter. indexToLocFormat changes because glyf is smaller.
SUBSET_CHANGES = {
    "head": {"indexToLocFormat"},
    "maxp": {"numGlyphs"},
    "hhea": {"numberOfHMetrics"},
}

# Some of the locations fonts_customization.xml uses, plus the axis extremes, where
# variation errors would show most.
LOCATIONS = {
    "google-sans": [
        {"wght": 400, "opsz": 17, "GRAD": 0},
        {"wght": 500, "opsz": 18, "GRAD": 0},
        {"wght": 600, "opsz": 17, "GRAD": 0},
        {"wght": 700, "opsz": 18, "GRAD": 0},
        {"wght": 700, "opsz": 17, "GRAD": -50},
        {"wght": 400, "opsz": 18, "GRAD": 200},
    ],
    "google-sans-flex": [
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


def file_sha256(path):
    with open(path, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()


def load_preprocess():
    module_specification = importlib.util.spec_from_file_location(
        "preprocess", os.path.join(TOOLS_DIRECTORY, "preprocess.py")
    )
    module = importlib.util.module_from_spec(module_specification)
    module_specification.loader.exec_module(module)
    return module


def is_private_use(codepoint):
    return 0xE000 <= codepoint <= 0xF8FF or 0xF0000 <= codepoint <= 0x10FFFD


def expected_codepoints(font, family, preprocess):
    # Restates the rule independently, so a bug in the code of preprocess.py is not
    # repeated here.
    from fontTools import unicodedata

    upstream_codepoints = set(font.getBestCmap())
    if family == "google-sans":
        codepoints = {
            codepoint
            for codepoint in upstream_codepoints
            if set(unicodedata.script_extension(chr(codepoint)))
            & preprocess.GOOGLE_SANS_SCRIPTS
        }
        exclusions = preprocess.GOOGLE_SANS_EXCLUDE
    else:
        codepoints = set(upstream_codepoints)
        exclusions = preprocess.GOOGLE_SANS_FLEX_EXCLUDE
    return {
        codepoint
        for codepoint in codepoints
        if codepoint not in exclusions and not is_private_use(codepoint)
    }


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

    pen = RecordingPen()
    glyph_set[name].draw(pen)
    return pen.value, glyph_set[name].width


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


def verify_font(font_definition, arguments, preprocess):
    from fontTools.ttLib import TTFont

    family = font_definition["family"]
    source_path = os.path.join(arguments.input, font_definition["source"])
    output_path = os.path.join(arguments.module, font_definition["output"])
    print(f"== {font_definition['output']}")
    check(
        file_sha256(source_path) == font_definition["sha256"],
        f"input sha256 mismatch for {font_definition['source']}",
    )
    source = TTFont(source_path)
    output = TTFont(output_path)

    check(
        sorted(source.keys()) == sorted(output.keys()),
        f"table set differs: {sorted(source.keys())} vs {sorted(output.keys())}",
    )
    for tag in IDENTICAL_TABLES:
        if tag in source:
            check(
                source.getTableData(tag) == output.getTableData(tag),
                f"{tag} is not byte-identical",
            )
    print(f"  identical tables: {[tag for tag in IDENTICAL_TABLES if tag in source]}")

    allowed = ALLOWED_CHANGES[family]
    for tag in ("head", "OS/2", "maxp", "hhea"):
        differences = compare_fields(
            tag,
            source,
            output,
            allowed.get(tag, set()) | SUBSET_CHANGES.get(tag, set()),
        )
        for field, before, after in differences:
            print(f"  {tag}.{field}: {before} -> {after}")
    if family == "google-sans":
        check(
            output["OS/2"].usWinAscent == preprocess.GOOGLE_SANS_WINDOWS_ASCENT,
            "usWinAscent",
        )
        check(
            output["OS/2"].usWinDescent == preprocess.GOOGLE_SANS_WINDOWS_DESCENT,
            "usWinDescent",
        )
    else:
        check(
            output["head"].yMax == preprocess.GOOGLE_SANS_FLEX_HEAD_Y_MAXIMUM,
            "Google Sans Flex head.yMax",
        )

    # cmap
    source_character_map = source.getBestCmap()
    output_character_map = output.getBestCmap()
    expected = expected_codepoints(source, family, preprocess)
    extra = sorted(set(output_character_map) - expected)[:20]
    missing = sorted(expected - set(output_character_map))[:20]
    check(
        set(output_character_map) == expected,
        f"cmap differs from rule: extra {extra} missing {missing}",
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
        f"  cmap: {len(output_character_map)} codepoints (input {len(source_character_map)})"
    )

    # Glyphs
    glyph_names = output.getGlyphOrder()
    kept_glyphs = set(glyph_names)
    unknown_glyphs = sorted(kept_glyphs - set(source.getGlyphOrder()))[:10]
    check(not unknown_glyphs, f"glyphs not in input: {unknown_glyphs}")
    logos = {
        glyph_name
        for codepoint, glyph_name in source_character_map.items()
        if is_private_use(codepoint)
    }
    check(not (kept_glyphs & logos), f"logo glyphs left: {sorted(kept_glyphs & logos)}")
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
    for location in LOCATIONS[family]:
        source_glyphs = source.getGlyphSet(location=location)
        output_glyphs = output.getGlyphSet(location=location)
        mismatches = [
            name
            for name in glyph_names
            if recorded(source_glyphs, name) != recorded(output_glyphs, name)
        ]
        check(
            not mismatches,
            f"outline or advance differs at {location} for {mismatches[:10]}",
        )
        print(f"  at {location}: {len(glyph_names)} glyphs identical: {not mismatches}")

    # Features
    for tag, wanted in (
        ("GSUB", font_definition["gsub_features"]),
        ("GPOS", font_definition["gpos_features"]),
    ):
        source_features = set(feature_lookups(source, tag))
        output_features = set(feature_lookups(output, tag))
        expected_features = source_features if wanted is None else set(wanted)
        check(
            output_features == expected_features,
            f"{tag} features {sorted(output_features)} != expected {sorted(expected_features)}",
        )
        removed = sorted(source_features - output_features)
        print(f"  {tag} features: {sorted(output_features)} (removed {removed})")
        source_scripts = script_tags(source, tag)
        output_scripts = script_tags(output, tag)
        wanted_scripts = font_definition["layout_scripts"]
        expected_scripts = (
            source_scripts if wanted_scripts is None else set(wanted_scripts) & source_scripts
        )
        check(
            output_scripts == expected_scripts,
            f"{tag} scripts {sorted(output_scripts)} != expected {sorted(expected_scripts)}",
        )
        print(f"  {tag} scripts: {sorted(output_scripts)}")
    # Contextual and chaining rules (lookup types 5 and 6) are not compared, because
    # that needs a shaping engine. Rules only reachable through removed scripts are
    # expected to go, so the input is compared through the kept scripts only.
    scripts = font_definition["layout_scripts"]
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
    source_kerning = flatten_kern(source, kept_glyphs, scripts)
    output_kerning = flatten_kern(output, kept_glyphs, scripts)
    kerning_differences = set(source_kerning.items()) ^ set(output_kerning.items())
    check(
        source_kerning == output_kerning,
        f"kern pairs differ: {len(kerning_differences)}",
    )
    print(f"  {rule_count} GSUB substitution rules of kept features identical: {same}")
    print(
        f"  {len(output_kerning)} default kern pairs identical: {source_kerning == output_kerning}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fonttools", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--module", default=os.path.dirname(TOOLS_DIRECTORY))
    arguments = parser.parse_args()
    sys.path.insert(0, os.path.abspath(arguments.fonttools))
    preprocess = load_preprocess()

    # A fresh run must reproduce the shipped files byte for byte, which proves they come
    # from the pinned inputs.
    with tempfile.TemporaryDirectory() as temporary_directory:
        subprocess.run(
            [
                sys.executable,
                "-I",
                os.path.join(TOOLS_DIRECTORY, "preprocess.py"),
                "--fonttools",
                arguments.fonttools,
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
            same = os.path.exists(shipped) and file_sha256(shipped) == file_sha256(
                os.path.join(temporary_directory, name)
            )
            check(same, f"{name} differs from a fresh preprocess.py run")
        print(f"fresh run reproduces {len(generated)} files: {not failures}")

    for font_definition in preprocess.FONTS:
        verify_font(font_definition, arguments, preprocess)

    print("RESULT: " + ("PASS" if not failures else f"FAIL ({len(failures)})"))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
