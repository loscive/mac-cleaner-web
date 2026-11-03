#!/bin/bash
# Mac Cleaner 빌드 스크립트
# Google Drive는 .app 안의 심볼릭 링크를 깨뜨리므로 임시 폴더에서 빌드한 뒤
# ~/Applications 에 설치한다. (Drive 폴더 안의 dist/ 는 사용하지 말 것)
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d /tmp/maccleaner-build.XXXXXX)"
DEST="$HOME/Applications"
trap 'rm -rf "$WORK"' EXIT

cp "$SRC/mac_cleaner.py" "$SRC/Mac Cleaner.spec" "$SRC/MacCleaner.icns" "$WORK/"
cd "$WORK"
/usr/bin/python3 -m venv venv
./venv/bin/pip install -q pyinstaller
./venv/bin/pyinstaller --noconfirm --clean "Mac Cleaner.spec"

mkdir -p "$DEST"
rm -rf "$DEST/Mac Cleaner.app"
# ditto는 심볼릭 링크·서명을 그대로 보존
ditto "dist/Mac Cleaner.app" "$DEST/Mac Cleaner.app"
codesign --verify --deep --strict "$DEST/Mac Cleaner.app"
echo "설치 완료: $DEST/Mac Cleaner.app"
