"""Pillow 13.0.0 (python-for-android 2026.5.9 builds 11.3.0, which has
known faults), from its release on PyPI. Built as python-for-android
builds Pillow, with setup.py changed the same way (setup.py.patch)."""
from os.path import join

from pythonforandroid.recipes.Pillow import PillowRecipe


class Pillow13Recipe(PillowRecipe):
    version = "13.0.0"
    url = ("https://files.pythonhosted.org/packages/source/p/pillow/"
           "pillow-{version}.tar.gz")
    sha256sum = ("cdcd1ba258178f7356d74170c9a81ba4"
                 "eab6a5d55748bc402a5a2a6fe6be3510")
    patches = ["setup.py.patch"]

    def get_recipe_env(self, arch, **kwargs):
        env = super().get_recipe_env(arch, **kwargs)
        # libjpeg-turbo 3 (our jpeg recipe) keeps its headers in src/ and
        # writes jconfig.h where it builds, beside the library.
        jpeg = self.get_recipe("jpeg", self.ctx).get_build_dir(arch.arch)
        env["JPEG_ROOT"] = f"{jpeg}:{join(jpeg, 'src')}"
        env["CFLAGS"] = env.get("CFLAGS", "") + f" -I{jpeg}"
        return env


recipe = Pillow13Recipe()
