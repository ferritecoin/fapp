"""cryptography 50.0.2 (python-for-android 2026.5.9 builds 46.0.3, which has
13 advisories), from its release on PyPI, against our OpenSSL 3.5. Its
build is python-for-android's own (Rust)."""
from pythonforandroid.recipes.cryptography import CryptographyRecipe


class Cryptography50Recipe(CryptographyRecipe):
    version = "50.0.2"
    url = ("https://files.pythonhosted.org/packages/source/c/cryptography/"
           "cryptography-{version}.tar.gz")
    sha256sum = ("7b46165bb56eb4704e2eaaf86f3c940d"
                 "19154535d9b0ca7d6d590b04060e00d5")

    def get_recipe_env(self, arch, **kwargs):
        env = super().get_recipe_env(arch, **kwargs)
        # 50's maturin will not name an Android wheel without the API level
        # ("Failed to determine Android API level"), which
        # python-for-android does not set. The level it builds for is the
        # one it renames the wheel with (PyProjectRecipe).
        env["ANDROID_API_LEVEL"] = str(self.ctx.ndk_api)
        return env


recipe = Cryptography50Recipe()
