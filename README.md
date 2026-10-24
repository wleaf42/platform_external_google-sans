# Google Sans and Google Sans Flex

Defines the `google-sans*` and `variable-*` font families used by AOSP system UI code.

`google-sans-fonts` installs the fonts to `/product/fonts`, the families to
`/product/etc/fonts_customization.xml`, and the overlays `GoogleSansConfigOverlay`,
`GoogleSansDocumentsUIOverlay` and `GoogleSansAvatarPickerOverlay` from
frameworks/base/packages/overlays.

## Source

The inputs are Google Sans 14.000 and Google Sans Flex 5.000 from `ofl/googlesans` and
`ofl/googlesansflex` of google/fonts. `config/upstream.yml` pins the commit and their
sha256.

## Modifications

The shipped fonts follow the Pixel fonts described in `config/reference/`, limited to
what upstream has:

- Coverage, GSUB and GPOS scripts and features, and the table set are the Pixel fonts'.
  This removes the scripts Google Sans has beyond Latin, Greek, Cyrillic and Hebrew, and
  the BASE table of both fonts and HVAR of Google Sans Flex. Minikin extends lines by the
  BASE per-script extents, which are larger than hhea and made lines taller than with the
  Pixel fonts. The advance variations of Google Sans Flex stay in gvar.
- usWinAscent/usWinDescent and head yMin/yMax are the Pixel fonts'.

`config/overrides.yml` lists the deliberate differences with their reasons:

- Private Use Area characters, the Google logo glyphs at U+E000..=U+E006 among them, and
  the ligatures producing the logos are removed.
- head xMin/xMax of Google Sans are recalculated from the kept glyphs.
- U+0002 and U+0009 of the Pixel Google Sans are missing upstream.

Outlines, advances and variation data of kept glyphs are unchanged.

`tools/generate_fonts.py --print-codepoints` prints the kept codepoints.

## Font families

`fonts_customization.xml` is written for this module. Its family names, weights, axis
values and fallbacks follow the Pixel system image, because AOSP code requests these
families by name (for example `variable-label-large-emphasized` for the status bar clock)
and the values decide how that text looks. Only these values were taken from Pixel, not
the file itself. The Pixel file also defines families for other fonts (Lato, Rubik and
others), which are left out because this module only provides Google Sans.

## Regenerating

```sh
cd "$ANDROID_BUILD_TOP/external/google-fonts/google-sans"
COMMIT=$(yq -r .commit config/upstream.yml)
git clone --filter=blob:none --no-checkout https://github.com/google/fonts /tmp/google-fonts
git -C /tmp/google-fonts sparse-checkout set ofl/googlesans ofl/googlesansflex
git -C /tmp/google-fonts checkout "$COMMIT"
TOOLS="--fonttools ../../fonttools/Lib --pyyaml ../../python/pyyaml/lib"
python3 -I tools/generate_fonts.py $TOOLS --input /tmp/google-fonts --output .
python3 -I tools/check_fonts.py $TOOLS --input /tmp/google-fonts
```

## Pixel font reference

`config/reference/` describes the Pixel fonts from `/product/fonts` of a Pixel factory
image, with their version and sha256. Only refreshing it needs the image, for example
extracted by adevtool:

```sh
python3 -I tools/extract_reference.py $TOOLS \
    --pixel-fonts ../../../vendor/adevtool/dl/unpacked/blazer-CP3A.261005.005/product/fonts \
    --image "blazer CP3A.261005.005"
```

## Differences from the Pixel fonts

- Google's clock fonts are not openly licensed, so `google-sans-flex-clock` uses Google Sans
  Flex itself and `google-sans-clock` is not defined.
- Google Sans Flex has no `GRAD` axis, so no `GRAD` values are set for it.
- Google Sans 14.000 is newer than the Pixel font (10.001). Some glyphs differ slightly
  (ł Ł ° ¼ ½ ¾ Ŋ ĳ ß 1 7 9) and some strings range from 2% narrower to 2.4% wider.

## License

SIL Open Font License 1.1 (see `OFL-GoogleSans.txt` and `OFL-GoogleSansFlex.txt`)
