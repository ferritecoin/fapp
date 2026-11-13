"""libjpeg-turbo 3.2.1 (python-for-android 2026.5.9 builds 2.0.1, from
2018), from its release tarball. Pillow reads and writes photos with it.
Its build is python-for-android's own."""
from pythonforandroid.recipes.jpeg import JpegRecipe


class Jpeg32Recipe(JpegRecipe):
    version = "3.2.1"
    url = ("https://github.com/libjpeg-turbo/libjpeg-turbo/releases/"
           "download/{version}/libjpeg-turbo-{version}.tar.gz")
    sha256sum = ("c27ad72a68da151177929aa969ae773a"
                 "2da5ac2af9ce946079314fdac8cece7f")


recipe = Jpeg32Recipe()
