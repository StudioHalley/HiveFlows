#!/usr/bin/env bash
# Build portal-client for all supported platforms.
# Output: dist/portal-client-<os>-<arch>[.exe]
# Usage: ./build.sh [version]
set -euo pipefail

VERSION="${1:-$(git describe --tags --always --dirty 2>/dev/null || echo dev)}"
COMMIT="${COMMIT:-$(git rev-parse --short HEAD 2>/dev/null || echo unknown)}"
PKG="github.com/hiveflows/portal-client/cmd/portal-client"
LDFLAGS="-s -w -X ${PKG}.Version=${VERSION} -X '${PKG}.CommitHash=${COMMIT}'"
DIST="dist"

mkdir -p "$DIST"
echo "Building portal-client ${VERSION} (commit ${COMMIT})…"

targets=(
  "linux/amd64"
  "linux/arm64"
  "darwin/amd64"
  "darwin/arm64"
  "windows/amd64"
)

for target in "${targets[@]}"; do
  GOOS="${target%/*}"
  GOARCH="${target#*/}"
  OUT="${DIST}/portal-client-${GOOS}-${GOARCH}"
  [ "$GOOS" = "windows" ] && OUT="${OUT}.exe"

  echo "  → ${GOOS}/${GOARCH}"
  GOOS="$GOOS" GOARCH="$GOARCH" CGO_ENABLED=0 \
    go build -trimpath -ldflags "$LDFLAGS" -o "$OUT" ./cmd/portal-client/
done

echo "Generating checksums…"
cd "$DIST"
sha256sum portal-client-* > checksums.sha256
cat checksums.sha256
echo "Done. Binaries in ${DIST}/"
