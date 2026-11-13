"""OpenSSL 3.5, the current long-term release (python-for-android
2026.5.9 builds 3.3.1), from the release OpenSSL publishes on GitHub.
Its build is python-for-android's own."""
from pythonforandroid.recipes.openssl import OpenSSLRecipe


class OpenSSL35Recipe(OpenSSLRecipe):
    version = "3.5.9"
    url = ("https://github.com/openssl/openssl/releases/download/"
           "openssl-{version}/openssl-{version}.tar.gz")
    sha256sum = ("603f5602e2eef00d77fbd429d34dcd58"
                 "22bb301757a1bc9cdb24c670f1eb859a")


recipe = OpenSSL35Recipe()
