#!/usr/bin/env python3
"""Write fonts_customization.xml, the named font families provided by this module.

Usage: python3 -I tools/generate_fonts_customization.py > fonts_customization.xml

The family names, weights and axis values are the ones Android system UI code and the
Material 3 typography of AOSP refer to (google-sans*, variable-*). The framework reads the
file from /product/etc/fonts_customization.xml (SystemFonts.OEM_XML) and the fonts from
/product/fonts/.

Google Sans Flex has no GRAD axis, so no GRAD value is set for it. No clock families
(google-sans-clock, google-sans-flex-clock) are defined because no openly licensed clock
fonts exist. Code using those names gets the default font.
"""

import sys
import xml.etree.ElementTree as ElementTree

GOOGLE_SANS = "GoogleSans-Regular.ttf"
GOOGLE_SANS_ITALIC = "GoogleSans-Italic.ttf"
GOOGLE_SANS_FLEX = "GoogleSansFlex-Regular.ttf"

# Material 3 type scale: (role, size, optical size, fallback family).
SCALE = [
    ("display", "large", 57, "google-sans"),
    ("display", "medium", 45, "google-sans"),
    ("display", "small", 36, "google-sans"),
    ("headline", "large", 32, "google-sans"),
    ("headline", "medium", 28, "google-sans"),
    ("headline", "small", 24, "google-sans"),
    ("title", "large", 22, "google-sans"),
    ("title", "medium", 16, "google-sans-text"),
    ("title", "small", 14, "google-sans-text"),
    ("label", "large", 14, "google-sans-text"),
    ("label", "medium", 12, "google-sans-text"),
    ("label", "small", 11, "google-sans-text"),
    ("body", "large", 16, "google-sans-text"),
    ("body", "medium", 14, "google-sans-text"),
    ("body", "small", 12, "google-sans-text"),
]

# Weights of each variable family: (regular, bold, heaviest) for the baseline and the
# emphasized variant of each role. Title (except large) and label use heavier weights.
BASELINE_WEIGHTS = {
    "display": (400, 700, 1000),
    "headline": (400, 700, 1000),
    "title": (400, 700, 1000),
    "label": (500, 800, 1000),
    "body": (400, 700, 1000),
}
EMPHASIZED_WEIGHTS = {
    "display": (500, 800, 1000),
    "headline": (500, 800, 1000),
    "title": (500, 800, 1000),
    "label": (600, 900, 1000),
    "body": (500, 800, 1000),
}
HEAVY_TITLES = {"medium", "small"}

ALL_WEIGHTS = range(100, 1000, 100)

INDENT = "  "


def add_font(parent, font_file, weight, style, axes):
    # The font file name is the text of the element. It is attached after indenting (see
    # attach_font_files) because ElementTree.indent only indents elements whose text is
    # empty.
    font = ElementTree.SubElement(parent, "font", weight=str(weight), style=style)
    font.set("file", font_file)
    for tag, value in axes:
        ElementTree.SubElement(font, "axis", tag=tag, stylevalue=str(value))


def add_google_sans_font(parent, font_file, weight, style, optical_size):
    # Google Sans: grade 0, the optical size of the family, weight as requested. The wght
    # axis of Google Sans ends at 700, and the font engine clamps higher values.
    axes = [("GRAD", 0), ("opsz", f"{optical_size:.1f}"), ("wght", weight)]
    add_font(parent, font_file, weight, style, axes)


def add_family(root, name, fallback=None):
    family = ElementTree.SubElement(
        root, "family", customizationType="new-named-family", name=name
    )
    if fallback:
        family.set("fallback", fallback)
    return family


def add_google_sans_family(
    root, name, optical_size, weights, styles=("normal", "italic")
):
    family = add_family(root, name)
    for style in styles:
        font_file = GOOGLE_SANS if style == "normal" else GOOGLE_SANS_ITALIC
        for weight in weights:
            add_google_sans_font(family, font_file, weight, style, optical_size)


def add_variable_family(root, name, weights, optical_size, fallback, emphasized):
    family = add_family(root, name, fallback)
    for weight in weights:
        axes = [("wght", weight), ("wdth", 100)]
        if emphasized:
            axes.append(("ROND", 100))
        axes.append(("opsz", f"{optical_size:.1f}"))
        add_font(family, GOOGLE_SANS_FLEX, weight, "normal", axes)


def add_type_scale(root, emphasized):
    weight_table = EMPHASIZED_WEIGHTS if emphasized else BASELINE_WEIGHTS
    for role, size, optical_size, fallback in SCALE:
        weights = weight_table[role]
        if role == "title" and size in HEAVY_TITLES:
            weights = weight_table["label"]
        name = f"variable-{role}-{size}" + ("-emphasized" if emphasized else "")
        add_variable_family(root, name, weights, optical_size, fallback, emphasized)
        if emphasized and (role, size) == ("display", "small"):
            add_variable_family(
                root,
                "variable-display-small-emphasized-light",
                (300, 600, 900),
                optical_size,
                fallback,
                emphasized=True,
            )


def add_google_sans_flex_family_list(root):
    # google-sans-flex: Google Sans Flex at every weight, upright and slanted, with Google
    # Sans as the fallback for characters that Google Sans Flex does not cover.
    family_list = ElementTree.SubElement(
        root,
        "family-list",
        customizationType="new-named-family",
        name="google-sans-flex",
    )
    google_sans_flex_family = ElementTree.SubElement(family_list, "family")
    for weight in ALL_WEIGHTS:
        add_font(
            google_sans_flex_family,
            GOOGLE_SANS_FLEX,
            weight,
            "normal",
            [("wght", weight)],
        )
    for weight in ALL_WEIGHTS:
        axes = [("wght", weight), ("slnt", -10)]
        add_font(google_sans_flex_family, GOOGLE_SANS_FLEX, weight, "italic", axes)
    google_sans_family = ElementTree.SubElement(family_list, "family")
    for weight in ALL_WEIGHTS:
        add_font(google_sans_family, GOOGLE_SANS, weight, "normal", [("wght", weight)])


def add_alias(root, name, target, weight):
    ElementTree.SubElement(root, "alias", name=name, to=target, weight=str(weight))


def attach_font_files(root):
    # Put each font file name before the indentation of its first axis, so that the file
    # name and every axis are on separate lines.
    for font in root.iter("font"):
        font.text = font.attrib.pop("file") + (font.text or "")


def build():
    root = ElementTree.Element("fonts-modification", version="1")
    root.append(
        ElementTree.Comment(
            " Generated by tools/generate_fonts_customization.py. Do not edit. "
        )
    )
    add_google_sans_family(root, "google-sans", 18, (400, 500, 600, 700))
    add_google_sans_flex_family_list(root)
    root.append(ElementTree.Comment(" Material 3 type scale "))
    add_type_scale(root, emphasized=False)
    root.append(ElementTree.Comment(" Material 3 emphasized type scale "))
    add_type_scale(root, emphasized=True)
    # Weight order as in the Pixel definition of google-sans-medium.
    medium = add_family(root, "google-sans-medium")
    for weight in (500, 800, 1000, 700):
        add_google_sans_font(medium, GOOGLE_SANS, weight, "normal", 18)
    add_google_sans_family(root, "google-sans-bold", 18, (700,), ("normal",))
    add_google_sans_family(root, "google-sans-text", 17, (400, 500, 600, 700))
    add_alias(root, "google-sans-text-medium", "google-sans-text", 500)
    add_alias(root, "google-sans-text-bold", "google-sans-text", 700)
    for name, weight in (
        ("google-sans-text-italic", 400),
        ("google-sans-text-medium-italic", 500),
        ("google-sans-text-bold-italic", 700),
    ):
        add_google_sans_family(root, name, 17, (weight,), ("italic",))
    ElementTree.indent(root, space=INDENT)
    attach_font_files(root)
    return ElementTree.ElementTree(root)


def main():
    build().write(sys.stdout.buffer, encoding="utf-8", xml_declaration=True)
    sys.stdout.buffer.write(b"\n")


if __name__ == "__main__":
    main()
