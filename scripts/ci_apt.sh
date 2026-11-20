#!/usr/bin/env bash
# System packages for a CI job, from actions/cache when it has them (#1095).
#
# The Ubuntu mirror the runners use is sometimes slow: 300 kB/s, which
# turned a 20-second apt step into 4 to 16 minutes on some shards. The job
# caches the .deb files apt downloaded (saved per runner image, restored
# from any image of the same OS); with them here, this installs the ones the
# image lacks with dpkg and never asks the mirror for anything, not even
# `apt-get update`. Without them, or if dpkg refuses them, it is the
# ordinary apt install, keeping what it downloads for the cache to save.
#
#   scripts/ci_apt.sh <cache dir> [--playwright "<browsers>"] <package>...
#
# --playwright also installs the system libraries of Playwright's browsers
# (`playwright install-deps`); playwright itself must be pip-installed first.
set -euo pipefail

cache=$1
shift
browsers=""
if [ "${1:-}" = "--playwright" ]; then
  browsers=$2
  shift 2
fi

mkdir -p "$cache/partial"

if compgen -G "$cache/*.deb" >/dev/null; then
  # Only the packages this image lacks: a cache saved on another image
  # (the runners are served from several at once) never downgrades or
  # replaces what the image has. A deb that needs a newer library than the
  # image's fails dpkg here, and apt below puts it right.
  missing=()
  for deb in "$cache"/*.deb; do
    pkg=$(dpkg-deb -f "$deb" Package)
    arch=$(dpkg-deb -f "$deb" Architecture)
    if ! dpkg-query -W -f='${Status}' "$pkg:$arch" 2>/dev/null | grep -q "ok installed"; then
      missing+=("$deb")
    fi
  done
  echo "${#missing[@]} of $(ls "$cache"/*.deb | wc -l) cached packages to install with dpkg"
  ok=1
  if [ "${#missing[@]}" -gt 0 ]; then
    sudo DEBIAN_FRONTEND=noninteractive dpkg -i "${missing[@]}" >/dev/null || ok=0
  fi
  # whole: every package asked for is in, and dpkg has nothing half-done
  if [ "$ok" = 1 ] && dpkg -s "$@" >/dev/null 2>&1 && [ -z "$(dpkg --audit)" ]; then
    echo "from the cache, without the mirror"
    exit 0
  fi
  echo "the cache did not cover it; apt as usual"
fi

# keep what apt downloads, in the cached folder
printf 'Dir::Cache::Archives "%s";\nAPT::Keep-Downloaded-Packages "true";\nBinary::apt::APT::Keep-Downloaded-Packages "true";\n' "$cache" \
  | sudo tee /etc/apt/apt.conf.d/99ci-keep-debs >/dev/null
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -f --no-install-recommends "$@"
if [ -n "$browsers" ]; then
  read -r -a names <<<"$browsers"
  python -m playwright install-deps "${names[@]}" # it runs apt through sudo
fi
sudo chown -R "$(id -u):$(id -g)" "$cache"
