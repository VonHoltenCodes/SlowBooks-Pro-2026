#!/usr/bin/env bash
# System packages for a CI job, from actions/cache when it has them (#1095).
#
# The Ubuntu mirror the runners use is sometimes slow: 300 kB/s, which
# turned a 20-second apt step into 4 to 16 minutes on some shards. The job
# caches the .deb files apt downloaded (key: the runner image's version and
# the package list, so a newer image starts a new cache); with them here,
# this installs them with dpkg and never asks the mirror for anything, not
# even `apt-get update`. Without them, or if dpkg refuses them, it is the
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
  echo "installing $(ls "$cache"/*.deb | wc -l) cached packages with dpkg"
  if sudo dpkg -i "$cache"/*.deb >/dev/null; then
    # what dpkg installed is what was asked for, if the list is whole
    if dpkg -s "$@" >/dev/null 2>&1; then
      echo "from the cache, without the mirror"
      exit 0
    fi
  fi
  echo "the cache did not cover it; apt as usual"
fi

# keep what apt downloads, in the cached folder
printf 'Dir::Cache::Archives "%s";\nAPT::Keep-Downloaded-Packages "true";\nBinary::apt::APT::Keep-Downloaded-Packages "true";\n' "$cache" \
  | sudo tee /etc/apt/apt.conf.d/99ci-keep-debs >/dev/null
sudo apt-get update
sudo apt-get install -y -f --no-install-recommends "$@"
if [ -n "$browsers" ]; then
  # shellcheck disable=SC2086  # a list of browser names
  python -m playwright install-deps $browsers  # it runs apt through sudo
fi
sudo chown -R "$(id -u):$(id -g)" "$cache"
