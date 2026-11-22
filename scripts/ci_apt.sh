#!/usr/bin/env bash
# System packages for a CI job, from actions/cache when it has them (#1095).
#
# The Ubuntu mirror the runners use is sometimes slow: 300 kB/s, which
# turned a 20-second apt step into 4 to 16 minutes on some shards. The job
# caches the .deb files apt downloaded, keyed on the runner image's version
# (several are served at once, so each has its own); with them here, this
# installs them with dpkg and never asks the mirror for anything, not even
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
  # the whole set apt chose for this image, so its versions agree with
  # each other and with the image (the cache key is the image's version:
  # a set saved on another image can need newer libraries than this one
  # has, which is how a restore across images broke dpkg on run 44653809362)
  echo "installing $(ls "$cache"/*.deb | wc -l) cached packages with dpkg"
  if sudo DEBIAN_FRONTEND=noninteractive dpkg -i "$cache"/*.deb >/dev/null \
    && dpkg -s "$@" >/dev/null 2>&1 && [ -z "$(dpkg --audit)" ]; then
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
