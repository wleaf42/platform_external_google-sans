# Google Sans and Google Sans Flex

## Pixel font reference

`config/reference/` describes the Pixel fonts from `/product/fonts` of a Pixel factory
image, with their version and sha256. Only refreshing it needs the image, for example
extracted by adevtool:

```sh
cd "$ANDROID_BUILD_TOP/external/google-fonts/google-sans"
TOOLS="--fonttools ../../fonttools/Lib --pyyaml ../../python/pyyaml/lib"
python3 -I tools/extract_reference.py $TOOLS \
    --pixel-fonts ../../../vendor/adevtool/dl/unpacked/blazer-CP3A.261005.005/product/fonts \
    --build CP3A.261005.005
```
