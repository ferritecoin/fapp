"""python-for-android recipe: libopus, for voice calls (v11).

main.py loads it with ctypes (opus_library) to code a call's speech as
Opus — about 24 kbit/s where ADPCM takes 80, with each packet carrying a
copy of the one before so a lost one is rebuilt. Nothing else links to it.

Built from the release tarball, checked against its published SHA-512.
Assembly and run-time CPU detection are left out, which keeps the build
plain C on every ABI; at 16 kHz a phone codes a call in a small fraction
of its time either way.
"""
from multiprocessing import cpu_count

import sh
from pythonforandroid.recipe import Recipe
from pythonforandroid.toolchain import current_directory, shprint


class LibOpusRecipe(Recipe):
    version = "1.5.2"
    url = ("https://github.com/xiph/opus/releases/download/"
           "v{version}/opus-{version}.tar.gz")
    sha512sum = ("78d963cd56d5504611f111e2b3606e236189a3585d65fae1ecdbec9bf"
                 "4545632b1956f11824328279a2d1ea2ecf441ebc11e455fb598d20a45"
                 "8df15185e95da4")
    built_libraries = {"libopus.so": ".libs"}

    def build_arch(self, arch):
        with current_directory(self.get_build_dir(arch.arch)):
            env = self.get_recipe_env(arch)
            flags = ["--host=" + arch.command_prefix,
                     "--enable-shared", "--disable-static",
                     "--disable-doc", "--disable-extra-programs",
                     "--disable-asm", "--disable-rtcd"]
            if arch.arch == "armeabi-v7a":
                flags.append("--disable-intrinsics")
            shprint(sh.Command("./configure"), *flags, _env=env)
            shprint(sh.make, f"-j{cpu_count()}", _env=env)


recipe = LibOpusRecipe()
