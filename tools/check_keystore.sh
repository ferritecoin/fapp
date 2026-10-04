# Sourced by .github/workflows/build-apk.yml before a release build.
#
#   check_keystore <keystore file> <alias>
#
# Proves the signing secrets work BEFORE a 20-minute build, and names the
# secret to fix when they do not. Reads STOREPASS and KEYPASS from the calling
# shell; a PKCS12 keystore has a single password, so KEYPASS is set to
# STOREPASS for one. Never prints a password. Returns non-zero on failure.
check_keystore() {
  local ks="$1" alias="$2" list info type until_raw until_s names
  if [ ! -s "$ks" ]; then
    echo "::error::FEXT_KEYSTORE_B64 decoded to nothing. Encode the .keystore file again (README: Release signing)."
    return 1
  fi
  if [ -z "${STOREPASS:-}" ]; then
    echo "::error::FEXT_KEYSTORE_PASSWORD is not set."
    return 1
  fi
  if ! list="$(keytool -list -keystore "$ks" -storepass "$STOREPASS" 2>&1)"; then
    echo "::error::The keystore will not open: FEXT_KEYSTORE_PASSWORD is wrong, or FEXT_KEYSTORE_B64 is not a keystore file."
    return 1
  fi
  if ! info="$(keytool -list -v -keystore "$ks" -storepass "$STOREPASS" -alias "$alias" 2>&1)"; then
    names="$(printf '%s\n' "$list" | sed -n 's/^\([^,]*\), .*PrivateKeyEntry.*/\1/p' | paste -sd ' ' -)"
    echo "::error::The keystore has no key named '$alias'. Set FEXT_KEY_ALIAS to: ${names:-(no keys found)}"
    return 1
  fi
  type="$(printf '%s\n' "$list" | sed -n 's/^Keystore type: *//p' | head -1)"
  if [ "$type" = "PKCS12" ]; then
    if [ "${KEYPASS:-}" != "$STOREPASS" ]; then
      echo "::warning::FEXT_KEY_PASSWORD differs from FEXT_KEYSTORE_PASSWORD, but a PKCS12 keystore has one password; the keystore password is used for the key."
    fi
    KEYPASS="$STOREPASS"
  elif ! keytool -certreq -keystore "$ks" -storepass "$STOREPASS" -alias "$alias" \
      -keypass "${KEYPASS:-}" -file /dev/null >/dev/null 2>&1; then
    echo "::error::FEXT_KEY_PASSWORD does not unlock the key '$alias'."
    return 1
  fi
  echo "Signing key ($type):"
  printf '%s\n' "$info" | grep -E '^(Alias name|Owner|Valid from):|^[[:space:]]*SHA256:' \
    | sed 's/^[[:space:]]*/  /'
  until_raw="$(printf '%s\n' "$info" | sed -n 's/^Valid from: .* until: //p' | head -1)"
  until_s="$(date -d "$until_raw" +%s 2>/dev/null || echo 0)"
  if [ "$until_s" -lt "$(( $(date +%s) + 20 * 365 * 86400 ))" ]; then
    echo "::warning::The signing certificate is valid only until ${until_raw:-an unknown date}. Every future update must be signed with this key, so the convention is 25+ years (keytool -validity 10000); keytool's default is 90 days."
  fi
  return 0
}
