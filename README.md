# Mac Cleaner

브라우저에서 쓰는 macOS 저장 공간 정리 도구입니다. Python 표준 라이브러리만 사용합니다.

- 사용자·시스템 캐시, 로그, 브라우저 캐시(Safari·Chrome·Firefox)
- 개발 도구 캐시(npm, pip, Homebrew, Yarn, CocoaPods, Xcode DerivedData)
- 마운트되지 않은 `~/Downloads/*.dmg`, 휴지통
- 비활성 메모리 정리(`purge`, 관리자 암호 필요)

## 설치 (Apple Silicon Mac)

1. [Releases](https://github.com/loscive/mac-cleaner-web/releases/latest)에서 `MacCleaner-*-arm64.dmg`를 내려받습니다.
2. DMG를 열고 `Mac Cleaner.app`을 `Applications` 폴더로 끌어 놓습니다.
3. 처음 실행할 때 "확인되지 않은 개발자" 경고가 나오면:
   **시스템 설정 → 개인정보 보호 및 보안** 맨 아래의 **그래도 열기**를 누릅니다.
   또는 터미널에서 다음을 실행합니다.

   ```bash
   xattr -dr com.apple.quarantine "/Applications/Mac Cleaner.app"
   ```

> Apple 개발자 인증서로 서명·공증하지 않은 앱이라 이 경고가 나옵니다. Intel Mac은 지원하지 않습니다.

## 소스에서 실행

```bash
python3 mac_cleaner.py
```

`http://127.0.0.1:18765`가 브라우저에서 열립니다. 탭을 닫으면 몇 분 안에 자동으로 종료됩니다.

## 앱 빌드

```bash
./build.sh
```

임시 폴더에서 PyInstaller로 빌드해 `~/Applications/Mac Cleaner.app`에 설치합니다(arm64).

## 보안

서버는 `127.0.0.1`에만 열립니다. 삭제 등 상태를 바꾸는 API는 POST와 실행할 때마다 새로 만드는 토큰이 있어야 하므로, 다른 웹사이트가 호출할 수 없습니다.

> 삭제는 영구적이며 되돌릴 수 없습니다. 삭제 전 확인 창의 목록을 꼭 확인하세요.
