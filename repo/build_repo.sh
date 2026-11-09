#!/usr/bin/env bash
# Run after building the release .deb in ../tweak.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p debs

shopt -s nullglob
packages=(../tweak/packages/*.deb)
if ((${#packages[@]} == 0)); then
    echo "No .deb found. Build the tweak first: cd ../tweak && make package PROXY_HOST=<proxy-host>"
    exit 1
fi

# Keep only the newly built package so Cydia cannot offer stale LAN-IP builds.
latest="$(ls -t "${packages[@]}" | head -n 1)"
expected_version="$(awk -F': ' '$1 == "Version" { print $2; exit }' ../tweak/control)"
actual_version="$(dpkg-deb -f "$latest" Version)"
if [[ "$actual_version" != "$expected_version"* ]]; then
    echo "Latest package is version $actual_version; expected a build of $expected_version."
    echo "Run make clean package in ../tweak, then rebuild this repo."
    exit 1
fi
rm -f debs/*.deb
cp "$latest" debs/
dpkg-scanpackages -m debs /dev/null > Packages
gzip -n -9 -kf Packages
bzip2 -9 -kf Packages

cat > Release <<'REL'
Origin: YT Legacy Fix
Label: YT Legacy Fix
Suite: stable
Version: 1.0
Codename: ios
Architectures: iphoneos-arm
Components: main
Description: Work on iOS 7 ONLY.
REL

echo "Repo built from $(basename "$latest"). Upload this folder to a static host."
