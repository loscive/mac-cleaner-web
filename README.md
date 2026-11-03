# Mac Cleaner

브라우저에서 쓰는 macOS 저장 공간 정리 도구입니다. Python 표준 라이브러리만 사용합니다.

- 사용자·시스템 캐시, 로그, 브라우저 캐시(Safari·Chrome·Firefox)
- 개발 도구 캐시(npm, pip, Homebrew, Yarn, CocoaPods, Xcode DerivedData)
- 마운트되지 않은 `~/Downloads/*.dmg`, 휴지통
- 비활성 메모리 정리(`purge`, 관리자 암호 필요)

## 실행

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
