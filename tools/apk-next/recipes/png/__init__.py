"""libpng 1.6.59 (python-for-android 2026.5.9 builds 1.6.37, from 2019),
from its release tarball. Its build is python-for-android's own."""
from pythonforandroid.recipes.png import PngRecipe


class Png16Recipe(PngRecipe):
    version = "1.6.59"
    url = ("https://downloads.sourceforge.net/project/libpng/libpng16/"
           "{version}/libpng-{version}.tar.xz")
    sha256sum = ("d80dd2a38a37f803cb9b6ac7b14bd6e7"
                 "4ddc3b654780a8380bdf93523fdb4389")


recipe = Png16Recipe()
