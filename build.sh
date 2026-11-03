#!/bin/bash
# Mac Cleaner 빌드 스크립트
# Google Drive는 .app 안의 심볼릭 링크를 깨뜨리므로 임시 폴더에서 빌드한 뒤
# ~/Applications 에 설치한다. (Drive 폴더 안의 dist/ 는 사용하지 말 것)
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d /tmp/maccleaner-build.XXXXXX)"
DEST="$HOME/Applications"
trap 'rm -rf "$WORK"' EXIT

cp "$SRC/mac_cleaner.py" "$SRC/Mac Cleaner.spec" "$SRC/MacCleaner.icns" \
   "$SRC/build-requirements.txt" "$WORK/"
cd "$WORK"
/usr/bin/python3 -m venv venv
# 버전·해시가 고정된 휠만 설치 (패키지 저장소가 변조돼도 다른 코드가 섞이지 않게)
./venv/bin/pip install -q --require-hashes --only-binary=:all: -r build-requirements.txt
./venv/bin/pyinstaller --noconfirm --clean "Mac Cleaner.spec"

mkdir -p "$DEST"
rm -rf "$DEST/Mac Cleaner.app"
# ditto는 심볼릭 링크·서명을 그대로 보존
ditto "dist/Mac Cleaner.app" "$DEST/Mac Cleaner.app"
codesign --verify --deep --strict "$DEST/Mac Cleaner.app"
echo "설치 완료: $DEST/Mac Cleaner.app"
