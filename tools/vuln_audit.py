"""vuln_audit.py — known vulnerabilities of exact Python package versions.

    python3 tools/vuln_audit.py name==version [name==version ...]
    python3 tools/vuln_audit.py -r desktop/requirements-exe.txt

CI runs it on the client's pinned packages (16.0, CLIENTDEPS): a pin with
an advisory fails the build until it is raised.

Asks PyPI's JSON API (https://pypi.org/pypi/NAME/VERSION/json), whose
"vulnerabilities" list is the Python Packaging Advisory Database as OSV
publishes it: the source pip-audit uses by default. One line per package,
"ok" or each advisory with its CVE, the versions that fix it and a summary.
Standard library only. Exit status 1 when any package has an advisory.

It covers what PyPI publishes: not the C libraries a wheel carries inside
it (OpenSSL in cryptography, FFmpeg in av, SDL in kivy_deps); check those
by their own versions (sbom.py lists them).
"""
import json
import sys
from pathlib import Path
import urllib.error
import urllib.request


def advisories(name: str, version: str) -> list:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return json.load(response).get("vulnerabilities", [])
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SystemExit(f"{name}=={version} is not on PyPI")
        raise


def pins_from(argv: list) -> list:
    if argv[:1] == ["-r"]:
        path = Path(argv[1])
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        path, lines = None, argv
    out = []
    for line in lines:
        line = line.split("#")[0].split(";")[0].split("--hash")[0].strip()
        if line.startswith("-r ") and path is not None:   # a file it includes
            out += pins_from(["-r", str(path.parent / line[3:].strip())])
        elif "==" in line:
            name, version = line.split("==", 1)
            out.append((name.strip(), version.strip().rstrip("\\").strip()))
    return out


def main(argv: list) -> int:
    found = 0
    for name, version in pins_from(argv):
        vulns = advisories(name, version)
        if not vulns:
            print(f"ok    {name}=={version}")
            continue
        found += 1
        print(f"VULN  {name}=={version}: {len(vulns)} advisories")
        for v in vulns:
            cves = ", ".join(a for a in v.get("aliases", [])
                             if a.startswith("CVE")) or "-"
            fixed = ", ".join(v.get("fixed_in") or []) or "no fix listed"
            text = " ".join((v.get("summary") or v.get("details") or "")
                            .split())[:120]
            print(f"        {v['id']} ({cves}) fixed in {fixed}: {text}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
