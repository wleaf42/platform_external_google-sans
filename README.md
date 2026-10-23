# Google Sans and Google Sans Flex

Defines the `google-sans*` and `variable-*` font families used by AOSP system UI code.

`google-sans-fonts` installs the fonts to `/product/fonts`, the families to
`/product/etc/fonts_customization.xml`, and the overlays `GoogleSansConfigOverlay`,
`GoogleSansDocumentsUIOverlay` and `GoogleSansAvatarPickerOverlay` from
frameworks/base/packages/overlays.

## Source

The inputs are Google Sans 14.000 and Google Sans Flex 5.000 from `ofl/googlesans` and
`ofl/googlesansflex` of google/fonts at commit `47d54ca1ebb14fd864695f1d39dc734f39017c33`.
`tools/preprocess.py` pins their sha256.

## Modifications

- Private Use Area characters, the Google logo glyphs at U+E000..U+E006 among them, and
  the ligatures producing the logos are removed.
- Google Sans covers only Latin, Greek, Cyrillic, Hebrew, Common and Inherited, minus
  `GOOGLE_SANS_EXCLUDE`. Its GSUB and GPOS scripts and features are limited to those of
  the Pixel build, usWinAscent/usWinDescent are 1057/382, and its head bounds are
  recalculated.
- Google Sans Flex drops `GOOGLE_SANS_FLEX_EXCLUDE` and has head.yMax set to 2007.
- The BASE table is removed from both, as the Pixel fonts have none. Minikin extends
  lines by its per-script extents, which are larger than hhea and made lines taller
  than with the Pixel fonts.
- Outlines, advances and variation data of kept glyphs are unchanged.

`tools/preprocess.py --print-codepoints` prints the kept codepoints.

## Font families

`fonts_customization.xml` is written for this module. Its family names, weights, axis
values and fallbacks follow the Pixel system image, because AOSP code requests these
families by name (for example `variable-label-large-emphasized` for the status bar clock)
and the values decide how that text looks. Only these values were taken from Pixel, not
the file itself. The Pixel file also defines families for other fonts (Lato, Rubik and
others), which are left out because this module only provides Google Sans.

## Regenerating

```sh
git clone --filter=blob:none --no-checkout https://github.com/google/fonts /tmp/google-fonts
git -C /tmp/google-fonts sparse-checkout set ofl/googlesans ofl/googlesansflex
git -C /tmp/google-fonts checkout 47d54ca1ebb14fd864695f1d39dc734f39017c33
cd "$ANDROID_BUILD_TOP/external/google-fonts/google-sans"
python3 -I tools/preprocess.py --fonttools ../../fonttools/Lib --input /tmp/google-fonts --output .
python3 -I tools/verify_preprocess.py --fonttools ../../fonttools/Lib --input /tmp/google-fonts
```

## Pixel font reference

The Pixel font values used here (coverage, exclusions, scripts, features, vertical
metrics) were compared against these Pixel fonts from `/product/fonts` of the blazer
factory image CP3A.261005.005:

| File | Version | sha256 |
| --- | --- | --- |
| `GoogleSans-Regular.ttf` | 10.001 | `def0489274d4c65092950acb870b7b65b610dca03d3bcfb3cd5c083189bf0956` |
| `GoogleSans-Italic.ttf` | 10.001 | `7ce25d8b6eea597fa7b97cba71db9315bec722dab56c8ed71662e81bf45fcc9a` |
| `GoogleSansFlex-Regular.ttf` | 4.001 | `83a85228956fcdfab605fc72f421eb0a416563d57d4eb0944396c02d874fe2a6` |

## Differences from the Pixel fonts

- Google's clock fonts are not openly licensed, so `google-sans-flex-clock` uses Google Sans
  Flex itself and `google-sans-clock` is not defined.
- Google Sans Flex has no `GRAD` axis, so no `GRAD` values are set for it.
- Google Sans 14.000 is newer than the Pixel font (10.001). Some glyphs differ slightly
  (ł Ł ° ¼ ½ ¾ Ŋ ĳ ß 1 7 9) and some strings range from 2% narrower to 2.4% wider.

## License

SIL Open Font License 1.1 (see `OFL-GoogleSans.txt` and `OFL-GoogleSansFlex.txt`)
