"""libwebp 1.6.0 (python-for-android 2026.5.9 builds 1.1.0, which has
CVE-2023-4863, the WebP overflow exploited in the wild: photos from others
are opened with it). Since 1.3 it also builds libsharpyuv, which libwebp
needs beside it. Its build is python-for-android's own."""
from pythonforandroid.recipes.libwebp import LibwebpRecipe


class Libwebp16Recipe(LibwebpRecipe):
    version = "1.6.0"
    sha256sum = ("e4ab7009bf0629fd11982d4c2aa83964"
                 "cf244cffba7347ecd39019a9e38c4564")
    built_libraries = dict(LibwebpRecipe.built_libraries,
                           **{"libsharpyuv.so": "installation/lib"})


recipe = Libwebp16Recipe()
