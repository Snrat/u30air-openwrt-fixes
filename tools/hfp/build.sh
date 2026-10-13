#!/bin/sh
# Build mu300-hfp as a static arm64 binary (no libbluetooth needed: it uses the kernel's Bluetooth sockets directly).
#   tools/hfp/build.sh [OUT]      default: tools/hfp/mu300-hfp    - install it as /opt/mu300/bin/mu300-hfp
set -eu
HERE=$(cd "$(dirname "$0")" && pwd); OUT=${1:-$HERE/mu300-hfp}
docker run --rm --platform linux/arm64 -v "$HERE":/w alpine sh -c \
  'apk add -q gcc musl-dev linux-headers && gcc -Wall -O2 -static -o /w/mu300-hfp.out /w/mu300-hfp.c'
mv "$HERE/mu300-hfp.out" "$OUT"; echo "built $OUT"
