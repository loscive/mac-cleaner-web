#!/usr/bin/env python3
"""Mac Cleaner - 웹 기반 macOS 저장 공간 정리 도구"""

import os, sys, shutil, json, threading, webbrowser, subprocess, re, time, secrets, plistlib
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

HOME   = Path.home()
PORT   = 18765
FROZEN = getattr(sys, "frozen", False)
# 페이지에 심어 두는 1회용 토큰: 다른 사이트가 삭제 API를 호출하지 못하게 막음
TOKEN  = secrets.token_urlsafe(32)

BIN = {
    "du":        "/usr/bin/du",
    "hdiutil":   "/usr/bin/hdiutil",
    "sysctl":    "/usr/sbin/sysctl",
    "vm_stat":   "/usr/bin/vm_stat",
    "purge":     "/usr/sbin/purge",
    "open":      "/usr/bin/open",
    "osascript": "/usr/bin/osascript",
}

# ─── 정리 기록 저장소 ─────────────────────────────────────────
CLEAN_LOG_PATH = HOME / "Library" / "Application Support" / "MacCleaner" / "clean_log.json"

def load_clean_log() -> dict:
    try:
        return json.loads(CLEAN_LOG_PATH.read_text())
    except Exception:
        return {}

def save_clean_log(key: str) -> None:
    try:
        CLEAN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        log = load_clean_log()
        log[key] = time.strftime("%Y-%m-%d %H:%M")
        CLEAN_LOG_PATH.write_text(json.dumps(log, ensure_ascii=False))
    except Exception:
        pass

def safe_print(*args, **kw):
    if sys.stdout is not None:
        try:
            print(*args, **kw)
        except Exception:
            pass

# ─── 정리 대상 ────────────────────────────────────────────────
def _chrome_paths():
    paths = [str(HOME / "Library" / "Caches" / "Google" / "Chrome")]
    base = HOME / "Library" / "Application Support" / "Google" / "Chrome"
    try:
        if base.exists():
            for p in base.iterdir():
                if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile")):
                    for sub in ("Cache", "Code Cache", "GPUCache"):
                        c = p / sub
                        if c.exists():
                            paths.append(str(c))
    except (OSError, PermissionError):
        pass
    return paths

TRASH = str(HOME / ".Trash")

TARGETS = [
    # ── 시스템 ──
    {"key": "user_cache", "label": "사용자 캐시",  "icon": "🗂",
     "desc": "앱 캐시 데이터",       "detail": "~/Library/Caches",
     "group": "system",
     "paths": [str(HOME / "Library" / "Caches")],
     # 브라우저 섹션에서 별도 처리하는 경로 제외 (중복 계산 방지)
     "excludes": [
         str(HOME / "Library" / "Caches" / "com.apple.Safari"),
         str(HOME / "Library" / "Caches" / "Google"),
         str(HOME / "Library" / "Caches" / "Firefox"),
     ]},
    {"key": "user_logs",  "label": "사용자 로그",  "icon": "📋",
     "desc": "앱 오류·충돌 로그",    "detail": "~/Library/Logs",
     "group": "system",
     "paths": [str(HOME / "Library" / "Logs")]},
    {"key": "sys_cache",  "label": "시스템 캐시",  "icon": "⚙️",
     "desc": "시스템 전역 캐시",      "detail": "/Library/Caches",
     "group": "system",
     "paths": ["/Library/Caches"]},
     # 다른 항목과 겹치는 하위 경로는 아래 _apply_auto_excludes()가 자동으로 제외
    # ── 브라우저 ──
    {"key": "safari",     "label": "Safari",        "icon": "🧭",
     "desc": "브라우저 임시 파일",    "detail": "~/Library/Caches/com.apple.Safari",
     "group": "browser",
     "paths": [str(HOME / "Library" / "Caches" / "com.apple.Safari")]},
    {"key": "chrome",     "label": "Chrome",         "icon": "🌐",
     "desc": "브라우저 임시 파일",    "detail": "~/Library/Caches/Google/Chrome",
     "group": "browser",
     "paths": []},
    {"key": "firefox",    "label": "Firefox",        "icon": "🦊",
     "desc": "브라우저 임시 파일",    "detail": "~/Library/Caches/Firefox",
     "group": "browser",
     "paths": [str(HOME / "Library" / "Caches" / "Firefox")]},
    # ── 개발자 ──
    {"key": "npm",        "label": "npm 캐시",       "icon": "📦",
     "desc": "Node.js 패키지 캐시",   "detail": "~/.npm",
     "group": "dev",
     "paths": [str(HOME / ".npm")]},
    {"key": "pip",        "label": "pip 캐시",       "icon": "🐍",
     "desc": "Python 패키지 캐시",    "detail": "~/Library/Caches/pip",
     "group": "dev",
     "paths": [str(HOME / "Library" / "Caches" / "pip"),
               str(HOME / ".cache" / "pip")]},
    {"key": "brew",       "label": "Homebrew",       "icon": "🍺",
     "desc": "패키지 다운로드 캐시",  "detail": "~/Library/Caches/Homebrew",
     "group": "dev",
     "paths": [str(HOME / "Library" / "Caches" / "Homebrew"),
               "/Library/Caches/Homebrew"]},
    {"key": "yarn",       "label": "Yarn 캐시",      "icon": "🧶",
     "desc": "Yarn 패키지 캐시",      "detail": "~/.yarn/cache",
     "group": "dev",
     "paths": [str(HOME / ".yarn" / "cache"),
               str(HOME / "Library" / "Caches" / "Yarn")]},
    {"key": "cocoapods",  "label": "CocoaPods",      "icon": "🍫",
     "desc": "iOS 의존성 캐시",       "detail": "~/Library/Caches/CocoaPods",
     "group": "dev",
     "paths": [str(HOME / "Library" / "Caches" / "CocoaPods")]},
    # ── 파일 ──
    # DMG는 캐시가 아닌 사용자 파일이므로 '전체 선택'에서 기본 제외
    {"key": "dmg",        "label": "DMG 파일",       "icon": "💿",
     "desc": "마운트되지 않은 디스크 이미지", "detail": "~/Downloads/*.dmg",
     "group": "files", "type": "dmg", "default": False,
     "paths": []},
    # Archives는 배포·크래시 분석용 원본이라 정리 대상에서 제외
    {"key": "xcode",      "label": "Xcode 캐시",     "icon": "🔨",
     "desc": "빌드·시뮬레이터 캐시",  "detail": "~/Library/Developer/Xcode",
     "group": "files",
     "paths": [str(HOME / "Library" / "Developer" / "Xcode" / "DerivedData"),
               str(HOME / "Library" / "Developer" / "Xcode" / "iOS DeviceSupport"),
               str(HOME / "Library" / "Developer" / "CoreSimulator" / "Caches")]},
    {"key": "trash",      "label": "휴지통",          "icon": "🗑",
     "desc": "삭제된 파일 임시 보관", "detail": "~/.Trash",
     "group": "files", "type": "trash",
     "paths": []},
]

def target_paths(t: dict) -> list:
    # Chrome 프로필은 앱 실행 중에도 추가될 수 있으므로 매번 새로 찾음
    return _chrome_paths() if t["key"] == "chrome" else t["paths"]

def _apply_auto_excludes() -> None:
    """다른 항목이 바로 아래 하위 폴더를 담당하면 상위 항목에서 제외 (중복 계산·삭제 방지)"""
    owner = {s: t["key"] for t in TARGETS for s in t["paths"]}
    for t in TARGETS:
        own = set(t["paths"])
        ex  = set(t.get("excludes", []))
        for s, k in owner.items():
            if k != t["key"] and str(Path(s).parent) in own:
                ex.add(s)
        if ex:
            t["excludes"] = sorted(ex)

_apply_auto_excludes()

# ─── 파일 크기 유틸 ───────────────────────────────────────────
def entry_size(p: Path) -> int:
    """심볼릭 링크는 따라가지 않고 링크 자체만 계산"""
    if p.is_symlink():
        return 0
    return dir_size(p) if p.is_dir() else p.stat().st_size

def dir_size(p: Path) -> int:
    """반복(iterative) 방식으로 재귀 스택 오버플로우 방지"""
    total = 0
    stack = [p]
    while stack:
        cur = stack.pop()
        try:
            for e in os.scandir(cur):
                try:
                    if e.is_file(follow_symlinks=False):
                        total += e.stat(follow_symlinks=False).st_size
                    elif e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
                except OSError:
                    pass
        except OSError:
            pass
    return total

def _mounted_images() -> set:
    try:
        r = subprocess.run([BIN["hdiutil"], "info", "-plist"],
                           capture_output=True, timeout=10)
        info = plistlib.loads(r.stdout) if r.returncode == 0 else {}
    except Exception:
        info = {}
    out = set()
    for img in info.get("images", []):
        p = img.get("image-path")
        if p:
            out.add(os.path.realpath(p))
    return out

def dmg_files():
    d = HOME / "Downloads"
    try:
        files = [f for f in d.iterdir()
                 if f.suffix.lower() == ".dmg" and f.is_file() and not f.is_symlink()]
    except OSError:
        return []
    mounted = _mounted_images()
    return [f for f in files if os.path.realpath(f) not in mounted]

# trash_size() 반환값: -1 = 권한 없음, -2 = 계산 시간 초과
def trash_size() -> int:
    try:
        r = subprocess.run([BIN["du"], "-sk", TRASH],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return -1
        parts = r.stdout.split()
        return int(parts[0]) * 1024 if parts else 0
    except subprocess.TimeoutExpired:
        return -2
    except Exception:
        return -1

def target_size(t: dict) -> int:
    try:
        if t.get("type") == "trash":
            return trash_size()
        if t.get("type") == "dmg":
            total = 0
            for f in dmg_files():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
            return total

        excludes = set(t.get("excludes", []))
        total = 0
        for s in target_paths(t):
            p = Path(s)
            if not p.exists():
                continue
            if p.is_file():
                total += p.stat().st_size
            elif excludes:
                try:
                    items = list(p.iterdir())
                except OSError:
                    continue
                for item in items:
                    if str(item) in excludes:
                        continue
                    # 항목 하나의 오류(깨진 링크 등)가 나머지 계산을 멈추지 않도록 개별 처리
                    try:
                        total += entry_size(item)
                    except OSError:
                        pass
            else:
                total += dir_size(p)
        return total
    except Exception:
        return 0

# ─── 디스크 정보 ──────────────────────────────────────────────
def get_disk_info() -> dict:
    try:
        st = shutil.disk_usage("/")
        return {
            "total": st.total,
            "used":  st.used,
            "free":  st.free,
            "pct":   round(st.used / st.total * 100, 1),
        }
    except Exception:
        return {"total": 0, "used": 0, "free": 0, "pct": 0}

# ─── 삭제 ────────────────────────────────────────────────────
def _make_writable(p: Path) -> None:
    import stat as _stat
    for root, dirs, files in os.walk(str(p), topdown=False):
        for name in files + dirs:
            fp = Path(root) / name
            # 링크를 따라가면 정리 폴더 밖 원본의 권한이 바뀌므로 건너뜀
            if fp.is_symlink():
                continue
            try:
                fp.chmod(fp.stat().st_mode | _stat.S_IWUSR)
            except OSError:
                pass
    try:
        p.chmod(p.stat().st_mode | _stat.S_IWUSR)
    except OSError:
        pass

def do_clean(t: dict) -> dict:
    try:
        if t.get("type") == "trash":
            # 전체 디스크 접근 권한이 없어 크기를 못 읽어도 Finder로는 비울 수 있음
            before = trash_size()
            try:
                r = subprocess.run(
                    [BIN["osascript"], "-e", 'tell application "Finder" to empty trash'],
                    capture_output=True, text=True, timeout=600)
            except subprocess.TimeoutExpired:
                return {"error": "timeout", "cleaned": 0}
            if r.returncode != 0:
                return {"error": "no_permission", "cleaned": 0}
            save_clean_log(t["key"])
            if before < 0:
                return {"cleaned": 0, "unknown": True}
            return {"cleaned": before}

        if t.get("type") == "dmg":
            cleaned = 0
            for f in dmg_files():
                try:
                    sz = f.stat().st_size
                    f.unlink()
                    cleaned += sz
                except OSError:
                    pass
            if cleaned:
                save_clean_log(t["key"])
            return {"cleaned": cleaned}

        excludes = set(t.get("excludes", []))
        cleaned = 0
        for s in target_paths(t):
            p = Path(s)
            if not p.exists():
                continue
            try:
                if p.is_file():
                    items = [p]
                elif excludes:
                    items = [i for i in p.iterdir() if str(i) not in excludes]
                else:
                    items = list(p.iterdir())
            except OSError:
                continue

            for item in items:
                try:
                    # 링크는 대상이 아닌 링크 자체만 지움 (rmtree는 링크에서 실패함)
                    if item.is_symlink():
                        item.unlink()
                        continue
                    sz = entry_size(item)
                    if item.is_dir():
                        _make_writable(item)
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
                    if not item.exists():
                        cleaned += sz
                    elif item.is_dir():
                        remaining = dir_size(item)
                        cleaned += max(0, sz - remaining)
                except OSError:
                    pass

        if cleaned:
            save_clean_log(t["key"])
        return {"cleaned": cleaned}
    except Exception as e:
        return {"error": str(e), "cleaned": 0}

# ─── 메모리 ───────────────────────────────────────────────────
def get_memory_info() -> dict:
    try:
        r = subprocess.run(["/usr/bin/pagesize"],
                           capture_output=True, text=True, timeout=5)
        page = int(r.stdout.strip()) if r.returncode == 0 else 4096
    except Exception:
        page = 4096
    try:
        r = subprocess.run([BIN["sysctl"], "-n", "hw.memsize"],
                           capture_output=True, text=True, timeout=5)
        total = int(r.stdout.strip()) if r.returncode == 0 else 0
    except Exception:
        total = 0
    try:
        r = subprocess.run([BIN["vm_stat"]],
                           capture_output=True, text=True, timeout=5)
        vm = r.stdout if r.returncode == 0 else ""
    except Exception:
        vm = ""

    def pages(key):
        m = re.search(rf"{key}:\s+([\d]+)", vm)
        return int(m.group(1)) * page if m else 0

    free       = pages("Pages free") + pages("Pages speculative")
    active     = pages("Pages active")
    inactive   = pages("Pages inactive")
    wired      = pages("Pages wired down")
    compressed = pages("Pages occupied by compressor")
    return {
        "total": total, "used": active + wired + compressed,
        "free": free, "inactive": inactive,
        "active": active, "wired": wired, "compressed": compressed,
    }

def do_purge() -> dict:
    before = get_memory_info()
    try:
        r = subprocess.run(
            [BIN["osascript"], "-e",
             f'do shell script "{BIN["purge"]}" with administrator privileges'],
            capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except Exception as e:
        return {"error": str(e)}
    if r.returncode != 0:
        return {"error": "cancelled"} if "-128" in r.stderr else {"error": r.stderr.strip()}
    after = get_memory_info()
    return {"freed": max(0, before["inactive"] - after["inactive"]), "after": after}

def trash_open_finder() -> dict:
    try:
        subprocess.run([BIN["open"], TRASH], capture_output=True, timeout=5)
        subprocess.run([BIN["osascript"], "-e", 'tell application "Finder" to activate'],
                       capture_output=True, timeout=5)
    except Exception:
        pass
    return {"ok": True}

def open_fda_settings() -> dict:
    try:
        subprocess.run([BIN["open"],
            "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"],
            capture_output=True, timeout=5)
    except Exception:
        pass
    return {"ok": True}

# ─── HTTP 서버 ────────────────────────────────────────────────
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

MUTATING_PATHS = {"/api/clean", "/api/purge", "/api/quit",
                  "/api/trash/open_finder", "/api/trash/open_fda"}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method):
        try:
            self._route(method)
        except Exception as e:
            try:
                self.send_json({"error": str(e)}, status=500)
            except Exception:
                pass

    def _host_ok(self) -> bool:
        # DNS 리바인딩 차단: 우리 주소로 들어온 요청만 허용
        return self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _route(self, method):
        parsed = urlparse(self.path)
        path   = parsed.path

        if not self._host_ok():
            self.send_json({"error": "forbidden"}, status=403)
            return

        # 상태를 바꾸는 API는 POST + 토큰 헤더만 허용 (다른 사이트의 요청 차단)
        if path in MUTATING_PATHS:
            if method != "POST" or not secrets.compare_digest(
                    self.headers.get("X-Token", ""), TOKEN):
                self.send_json({"error": "forbidden"}, status=403)
                return
        elif method != "GET":
            self.send_json({"error": "method not allowed"}, status=405)
            return

        if path in ("/", "/index.html"):
            # excludes 키는 프론트엔드에 불필요하므로 제외
            skip = {"paths", "excludes"}
            tj   = json.dumps(
                [{k: v for k, v in t.items() if k not in skip} for t in TARGETS],
                ensure_ascii=False)
            body = (HTML.replace("__TARGETS__", tj)
                        .replace("__TOKEN__", TOKEN)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/scan":
            self.send_json({t["key"]: target_size(t) for t in TARGETS})
            return

        if path == "/api/scan_one":
            key = parse_qs(parsed.query).get("key", [None])[0]
            t   = next((x for x in TARGETS if x["key"] == key), None)
            sz  = target_size(t) if t else 0
            self.send_json({key: sz})
            return

        if path == "/api/clean":
            key = parse_qs(parsed.query).get("key", [None])[0]
            t   = next((x for x in TARGETS if x["key"] == key), None)
            self.send_json(do_clean(t) if t else {"error": "not found"})
            return

        if path == "/api/diskinfo":
            self.send_json(get_disk_info())
            return

        if path == "/api/cleanlog":
            self.send_json(load_clean_log())
            return

        if path == "/api/trash/open_finder":
            self.send_json(trash_open_finder())
            return
        if path == "/api/trash/open_fda":
            self.send_json(open_fda_settings())
            return

        if path == "/api/memory":
            self.send_json(get_memory_info())
            return
        if path == "/api/purge":
            self.send_json(do_purge())
            return

        if path == "/api/ping":
            global _last_ping
            _last_ping = time.time()
            self.send_json({"ok": True})
            return

        if path == "/api/quit":
            self.send_json({"ok": True})
            threading.Thread(
                target=lambda: (time.sleep(0.3), os._exit(0)),
                daemon=True).start()
            return

        self.send_response(404)
        self.end_headers()


# ─── 서버·단일 인스턴스 제어 ──────────────────────────────────
_server_ready = threading.Event()
_server_error: str = ""
CTRL_PORT = 18764

def _try_wake_existing() -> bool:
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", CTRL_PORT)) == 0:
                s.sendall(b"reopen\n")
                return True
    except Exception:
        pass
    return False

def _ctrl_server():
    import socket
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", CTRL_PORT))
        srv.listen(5)
        while True:
            try:
                conn, _ = srv.accept()
                msg = conn.recv(32).decode("utf-8", "ignore").strip()
                conn.close()
                if msg == "reopen":
                    webbrowser.open(f"http://127.0.0.1:{PORT}")
            except Exception:
                pass
    except Exception:
        pass

_last_ping: float = 0.0
# 브라우저는 백그라운드 탭의 타이머를 최대 1분에 한 번으로 늦추므로 여유 있게 설정
_PING_TIMEOUT   = 180
_WATCHDOG_GRACE = 25

def _watchdog():
    time.sleep(_WATCHDOG_GRACE)
    while True:
        time.sleep(3)
        if _last_ping > 0 and (time.time() - _last_ping) > _PING_TIMEOUT:
            safe_print("브라우저 닫힘 감지 → 자동 종료")
            os._exit(0)

def _find_port(start: int) -> int:
    import socket
    for p in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start

def start_server():
    global PORT, _server_error
    try:
        srv = ThreadedHTTPServer(("127.0.0.1", PORT), Handler)
        _server_ready.set()
        srv.serve_forever()
    except OSError as e:
        _server_error = str(e)
        _server_ready.set()


# ─── HTML ─────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>Mac Cleaner</title>
<style>
/* ── CSS 변수 (라이트 모드 기본) ── */
:root{
  --bg:#F2F2F7;--card:#fff;--topbar:#fff;--sidebar:#fff;
  --border:#D1D1D6;--text:#1C1C1E;--text2:#3A3A3C;--text3:#636366;--text4:#8E8E93;
  --nav-hover:#F2F2F7;--nav-active-bg:#E5F0FF;--nav-active:#007AFF;--nav-active-sz:#5BABFF;
  --divider:#F2F2F7;--badge-bg:#E5F0FF;--badge-txt:#007AFF;
  --btn-gray-bg:#E5E5EA;--btn-gray-txt:#1C1C1E;--btn-gray-hover:#D1D1D6;
  --prog-bar:#E5E5EA;--mem-stat-bg:#F9F9FB;
  --trash-warn-bg:#FFF8EC;--trash-warn-border:#FFD60A;--trash-divider:#FFE88A;
  --disk-bar-bg:#E5E5EA;--select-border:#F2F2F7;
  --tile-bg:#fff;--toast-bg:#1C1C1E;--toast-txt:#fff;
  --detail-path-bg:#F2F2F7;
}
@media(prefers-color-scheme:dark){
  :root{
    --bg:#000;--card:#1C1C1E;--topbar:#1C1C1E;--sidebar:#1C1C1E;
    --border:#3A3A3C;--text:#F2F2F7;--text2:#EBEBF5;--text3:#AEAEB2;--text4:#636366;
    --nav-hover:#2C2C2E;--nav-active-bg:#1A3050;--nav-active:#5BABFF;--nav-active-sz:#90CAFF;
    --divider:#2C2C2E;--badge-bg:#1A3050;--badge-txt:#5BABFF;
    --btn-gray-bg:#2C2C2E;--btn-gray-txt:#F2F2F7;--btn-gray-hover:#3A3A3C;
    --prog-bar:#3A3A3C;--mem-stat-bg:#2C2C2E;
    --trash-warn-bg:#2C2200;--trash-warn-border:#665000;--trash-divider:#4A3800;
    --disk-bar-bg:#3A3A3C;--select-border:#3A3A3C;
    --tile-bg:#1C1C1E;--toast-bg:#F2F2F7;--toast-txt:#1C1C1E;
    --detail-path-bg:#2C2C2E;
  }
}

*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'SF Pro Text','Helvetica Neue',sans-serif;
     background:var(--bg);color:var(--text);display:flex;flex-direction:column;height:100vh;overflow:hidden}

/* ── 헤더 ── */
.topbar{background:var(--topbar);border-bottom:1px solid var(--border);padding:0 20px;
        height:54px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0;z-index:20}
.topbar h1{font-size:16px;font-weight:700;letter-spacing:-.2px}
.topbar-right{display:flex;align-items:center;gap:8px}
#scanBtn{background:var(--btn-gray-bg);color:var(--btn-gray-txt);border:none;border-radius:8px;
         padding:6px 13px;font-size:13px;font-weight:500;cursor:pointer}
#scanBtn:hover{background:var(--btn-gray-hover)}
#quitBtn{background:transparent;color:var(--text4);font-size:15px;
         padding:4px 8px;border-radius:6px;line-height:1;border:none;cursor:pointer}
#quitBtn:hover{background:#FFE5E5;color:#FF3B30}
#scanBadge{font-size:12px;color:var(--badge-txt);background:var(--badge-bg);
           padding:4px 10px;border-radius:12px;font-weight:600;white-space:nowrap}

/* ── 디스크 바 ── */
.diskbar{background:var(--topbar);border-bottom:1px solid var(--border);
         padding:6px 20px;display:flex;align-items:center;gap:12px;flex-shrink:0}
.diskbar-label{font-size:11px;color:var(--text4);white-space:nowrap}
.diskbar-track{flex:1;height:5px;background:var(--disk-bar-bg);border-radius:3px;overflow:hidden}
.diskbar-fill{height:100%;border-radius:3px;background:#007AFF;transition:width .6s ease}
.diskbar-fill.warn{background:#FF9500}
.diskbar-fill.crit{background:#FF3B30}
.diskbar-free{font-size:11px;color:var(--text4);white-space:nowrap}

/* ── 레이아웃 ── */
.layout{display:flex;flex:1;overflow:hidden}

/* ── 사이드바 ── */
aside{width:210px;flex-shrink:0;background:var(--sidebar);border-right:1px solid var(--border);
      overflow-y:auto;padding:10px 0}
.nav-section{padding:0 8px;margin-bottom:2px}
.nav-label{font-size:10px;font-weight:700;color:var(--text4);text-transform:uppercase;
           letter-spacing:.7px;padding:8px 10px 4px}
.nav-item{display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:8px;
          cursor:pointer;font-size:13px;font-weight:500;color:var(--text2);
          transition:background .12s;user-select:none}
.nav-item:hover{background:var(--nav-hover)}
.nav-item.active{background:var(--nav-active-bg);color:var(--nav-active)}
.nav-item .nav-icon{font-size:16px;width:20px;text-align:center;flex-shrink:0}
.nav-item .nav-txt{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nav-item .nav-sz{font-size:11px;color:var(--text4);font-weight:400}
.nav-item.active .nav-sz{color:var(--nav-active-sz)}
.nav-divider{height:1px;background:var(--divider);margin:5px 10px}

/* ── 콘텐츠 ── */
.content{flex:1;overflow-y:auto;padding:24px 28px}
.page{display:none}
.page.active{display:block}
.page-title{font-size:21px;font-weight:700;margin-bottom:5px}
.page-sub{font-size:13px;color:var(--text4);margin-bottom:20px}

/* ── 카드 ── */
.card{background:var(--card);border-radius:14px;box-shadow:0 1px 3px rgba(0,0,0,.08);
      margin-bottom:12px;overflow:hidden}

/* ── 대시보드 타일 ── */
.dash-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:16px}
.dash-tile{background:var(--tile-bg);border-radius:12px;padding:16px;
           box-shadow:0 1px 3px rgba(0,0,0,.07);cursor:pointer;transition:.15s;
           display:flex;flex-direction:column;gap:5px}
.dash-tile:hover{transform:translateY(-2px);box-shadow:0 4px 12px rgba(0,0,0,.12)}
.dash-tile .t-icon{font-size:24px}
.dash-tile .t-label{font-size:12px;font-weight:600;color:var(--text2)}
.dash-tile .t-size{font-size:17px;font-weight:700;margin-top:2px}
.dash-tile .t-size.big{color:#FF3B30}.dash-tile .t-size.med{color:#FF9500}
.dash-tile .t-size.sml{color:#34C759}.dash-tile .t-size.nil{color:var(--text4)}
.dash-tile .t-size.err{color:#FF9500}
.dash-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}

/* ── 상세 ── */
.detail-hero{background:var(--card);border-radius:14px;padding:22px;margin-bottom:12px;
             box-shadow:0 1px 3px rgba(0,0,0,.07);display:flex;align-items:center;gap:18px}
.detail-hero .big-icon{font-size:48px;flex-shrink:0}
.detail-hero h2{font-size:19px;font-weight:700;margin-bottom:3px}
.detail-hero .detail-desc{font-size:13px;color:var(--text3);margin-bottom:8px}
.detail-hero .detail-path{font-size:11px;color:var(--text4);background:var(--detail-path-bg);
                           display:inline-block;padding:3px 8px;border-radius:6px;font-family:monospace}
.detail-hero .last-cleaned{font-size:11px;color:var(--text4);margin-top:6px}
.size-display{font-size:30px;font-weight:700;margin-bottom:14px}
.size-display.big{color:#FF3B30}.size-display.med{color:#FF9500}
.size-display.sml{color:#34C759}.size-display.nil{color:var(--text4)}
.size-display.err{color:#FF9500}

/* ── 버튼 ── */
button{border:none;cursor:pointer;font-family:inherit;border-radius:10px;
       font-size:14px;font-weight:500;padding:10px 20px;transition:.15s}
.btn-primary{background:#007AFF;color:#fff;font-weight:700}
.btn-primary:hover{background:#0051D5}
.btn-primary:disabled{background:var(--text4);cursor:not-allowed}
.btn-gray{background:var(--btn-gray-bg);color:var(--btn-gray-txt)}
.btn-gray:hover{background:var(--btn-gray-hover)}
.btn-danger{background:#FF3B30;color:#fff;font-weight:700}
.btn-danger:hover{background:#CC2A21}
.btn-danger:disabled{background:var(--text4);cursor:not-allowed}
.btn-orange{background:#FF9500;color:#fff;font-weight:700}
.btn-orange:hover{background:#E08800}
.btn-orange:disabled{background:var(--text4);cursor:not-allowed}
.btn-sm{padding:7px 13px;font-size:13px;border-radius:8px}

/* ── 메모리 ── */
.mem-bar-wrap{background:var(--prog-bar);border-radius:8px;height:14px;overflow:hidden;
              display:flex;margin:12px 0 10px}
.mem-bar-used    {background:#007AFF;height:100%;transition:width .6s ease}
.mem-bar-inactive{background:#FF9500;height:100%;transition:width .6s ease}
.mem-bar-free    {background:#34C759;height:100%;transition:width .6s ease}
.mem-legend{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:16px}
.mem-legend-item{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text3)}
.mem-legend-dot{width:9px;height:9px;border-radius:50%}
.mem-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:16px}
.mem-stat{background:var(--mem-stat-bg);border-radius:10px;padding:13px;text-align:center}
.mem-stat .val{font-size:19px;font-weight:700;margin-bottom:3px}
.mem-stat .lbl{font-size:11px;color:var(--text4)}
.mem-stat.s-used .val{color:#007AFF}
.mem-stat.s-free .val{color:#34C759}
.mem-stat.s-inactive .val{color:#FF9500}

/* ── 휴지통 권한 박스 ── */
.trash-perm-box{background:var(--trash-warn-bg);border:1px solid var(--trash-warn-border);
                border-radius:12px;padding:16px 18px;margin-bottom:14px}
.tpb-title{font-size:14px;font-weight:700;margin-bottom:5px}
.tpb-desc{font-size:13px;color:var(--text3);line-height:1.65;margin-bottom:12px}
.tpb-option{padding:8px 0}
.tpb-opt-title{font-size:13px;font-weight:600;margin-bottom:4px}
.tpb-opt-desc{font-size:12px;color:var(--text3);line-height:1.55;margin-bottom:10px}
.tpb-divider{height:1px;background:var(--trash-divider);margin:4px 0}

/* ── 진행바 ── */
.prog-wrap{margin-bottom:12px;display:none}
.prog-label{font-size:12px;color:var(--text4);margin-bottom:5px}
progress{width:100%;height:6px;border-radius:3px;appearance:none;-webkit-appearance:none;border:none}
progress::-webkit-progress-bar{background:var(--prog-bar);border-radius:3px}
progress::-webkit-progress-value{background:#007AFF;border-radius:3px;transition:width .3s}

/* ── 토스트 ── */
.toast{position:fixed;bottom:26px;left:50%;transform:translateX(-50%) translateY(40px);
       background:var(--toast-bg);color:var(--toast-txt);padding:11px 22px;border-radius:12px;
       font-size:14px;font-weight:500;opacity:0;transition:.3s;pointer-events:none;
       box-shadow:0 4px 20px rgba(0,0,0,.25);z-index:999;white-space:nowrap}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}

/* ── 선택 행 ── */
.select-row{display:flex;align-items:center;gap:10px;padding:10px 0;
            border-bottom:1px solid var(--select-border);margin-bottom:12px}
.row-check{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text3)}
.row-check input{width:16px;height:16px;accent-color:#007AFF;cursor:pointer}
</style>
</head>
<body>

<div class="topbar">
  <h1>🧹 Mac Cleaner</h1>
  <div class="topbar-right">
    <span id="scanBadge">스캔 중...</span>
    <button id="scanBtn" onclick="startScan()">🔍 전체 스캔</button>
    <button id="quitBtn" onclick="quitApp()" title="앱 종료">✕</button>
  </div>
</div>

<!-- 디스크 사용량 바 -->
<div class="diskbar" id="diskbar" style="display:none">
  <span class="diskbar-label" id="diskUsed">--</span>
  <div class="diskbar-track"><div class="diskbar-fill" id="diskFill" style="width:0%"></div></div>
  <span class="diskbar-free" id="diskFree">여유 --</span>
</div>

<div class="layout">
<aside>
  <div class="nav-section">
    <div class="nav-item active" onclick="goto('dashboard')" id="nav-dashboard">
      <span class="nav-icon">📊</span><span class="nav-txt">전체 요약</span>
    </div>
    <div class="nav-item" onclick="goto('memory')" id="nav-memory">
      <span class="nav-icon">🧠</span><span class="nav-txt">메모리 정리</span>
    </div>
  </div>
  <div class="nav-divider"></div>
  <div class="nav-section">
    <div class="nav-label">시스템</div>
    <div class="nav-item" onclick="goto('user_cache')" id="nav-user_cache">
      <span class="nav-icon">🗂</span><span class="nav-txt">사용자 캐시</span>
      <span class="nav-sz" id="nsz-user_cache"></span>
    </div>
    <div class="nav-item" onclick="goto('user_logs')" id="nav-user_logs">
      <span class="nav-icon">📋</span><span class="nav-txt">사용자 로그</span>
      <span class="nav-sz" id="nsz-user_logs"></span>
    </div>
    <div class="nav-item" onclick="goto('sys_cache')" id="nav-sys_cache">
      <span class="nav-icon">⚙️</span><span class="nav-txt">시스템 캐시</span>
      <span class="nav-sz" id="nsz-sys_cache"></span>
    </div>
  </div>
  <div class="nav-divider"></div>
  <div class="nav-section">
    <div class="nav-label">브라우저</div>
    <div class="nav-item" onclick="goto('safari')" id="nav-safari">
      <span class="nav-icon">🧭</span><span class="nav-txt">Safari</span>
      <span class="nav-sz" id="nsz-safari"></span>
    </div>
    <div class="nav-item" onclick="goto('chrome')" id="nav-chrome">
      <span class="nav-icon">🌐</span><span class="nav-txt">Chrome</span>
      <span class="nav-sz" id="nsz-chrome"></span>
    </div>
    <div class="nav-item" onclick="goto('firefox')" id="nav-firefox">
      <span class="nav-icon">🦊</span><span class="nav-txt">Firefox</span>
      <span class="nav-sz" id="nsz-firefox"></span>
    </div>
  </div>
  <div class="nav-divider"></div>
  <div class="nav-section">
    <div class="nav-label">개발자</div>
    <div class="nav-item" onclick="goto('npm')" id="nav-npm">
      <span class="nav-icon">📦</span><span class="nav-txt">npm 캐시</span>
      <span class="nav-sz" id="nsz-npm"></span>
    </div>
    <div class="nav-item" onclick="goto('pip')" id="nav-pip">
      <span class="nav-icon">🐍</span><span class="nav-txt">pip 캐시</span>
      <span class="nav-sz" id="nsz-pip"></span>
    </div>
    <div class="nav-item" onclick="goto('brew')" id="nav-brew">
      <span class="nav-icon">🍺</span><span class="nav-txt">Homebrew</span>
      <span class="nav-sz" id="nsz-brew"></span>
    </div>
    <div class="nav-item" onclick="goto('yarn')" id="nav-yarn">
      <span class="nav-icon">🧶</span><span class="nav-txt">Yarn 캐시</span>
      <span class="nav-sz" id="nsz-yarn"></span>
    </div>
    <div class="nav-item" onclick="goto('cocoapods')" id="nav-cocoapods">
      <span class="nav-icon">🍫</span><span class="nav-txt">CocoaPods</span>
      <span class="nav-sz" id="nsz-cocoapods"></span>
    </div>
  </div>
  <div class="nav-divider"></div>
  <div class="nav-section">
    <div class="nav-label">파일</div>
    <div class="nav-item" onclick="goto('dmg')" id="nav-dmg">
      <span class="nav-icon">💿</span><span class="nav-txt">DMG 파일</span>
      <span class="nav-sz" id="nsz-dmg"></span>
    </div>
    <div class="nav-item" onclick="goto('xcode')" id="nav-xcode">
      <span class="nav-icon">🔨</span><span class="nav-txt">Xcode 캐시</span>
      <span class="nav-sz" id="nsz-xcode"></span>
    </div>
    <div class="nav-item" onclick="goto('trash')" id="nav-trash">
      <span class="nav-icon">🗑</span><span class="nav-txt">휴지통</span>
      <span class="nav-sz" id="nsz-trash"></span>
    </div>
  </div>
</aside>

<div class="content">
  <div class="prog-wrap" id="progWrap">
    <div class="prog-label" id="progLabel"></div>
    <progress id="prog" max="100" value="0"></progress>
  </div>

  <!-- 대시보드 -->
  <div class="page active" id="page-dashboard">
    <div class="page-title">전체 요약</div>
    <div class="page-sub">항목을 클릭하면 상세 페이지로 이동합니다.</div>
    <div class="dash-grid" id="dashGrid"></div>
    <div class="card" style="padding:16px">
      <div class="select-row">
        <label class="row-check">
          <input type="checkbox" id="chkAll" checked onchange="syncAll()"> 전체 선택
        </label>
        <span style="font-size:12px;color:var(--text4)" id="selCount"></span>
      </div>
      <div id="dashCheckList"
           style="display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:14px"></div>
      <div class="dash-actions">
        <button class="btn-gray" onclick="startScan()">🔍 다시 스캔</button>
        <button class="btn-primary" id="btnCleanAll" onclick="cleanSelected()" disabled>
          🗑 선택 항목 삭제
        </button>
      </div>
    </div>
  </div>

  <!-- 메모리 -->
  <div class="page" id="page-memory">
    <div class="page-title">🧠 메모리 정리</div>
    <div class="page-sub">비활성 메모리(RAM)를 해제해 여유 공간을 확보합니다.</div>
    <div class="card" style="padding:20px">
      <div style="color:var(--text4);font-size:13px" id="memLoading">메모리 정보 불러오는 중...</div>
      <div id="memBody" style="display:none">
        <div class="mem-bar-wrap">
          <div class="mem-bar-used"     id="barUsed"></div>
          <div class="mem-bar-inactive" id="barInactive"></div>
          <div class="mem-bar-free"     id="barFree"></div>
        </div>
        <div class="mem-legend">
          <div class="mem-legend-item">
            <div class="mem-legend-dot" style="background:#007AFF"></div>사용 중
          </div>
          <div class="mem-legend-item">
            <div class="mem-legend-dot" style="background:#FF9500"></div>비활성 (정리 가능)
          </div>
          <div class="mem-legend-item">
            <div class="mem-legend-dot" style="background:#34C759"></div>여유
          </div>
        </div>
        <div class="mem-stats">
          <div class="mem-stat s-used">
            <div class="val" id="mUsed">--</div><div class="lbl">사용 중</div>
          </div>
          <div class="mem-stat s-inactive">
            <div class="val" id="mInact">--</div><div class="lbl">비활성</div>
          </div>
          <div class="mem-stat s-free">
            <div class="val" id="mFree">--</div><div class="lbl">여유</div>
          </div>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;
                    background:var(--mem-stat-bg);border-radius:10px;padding:13px 16px">
          <div>
            <div style="font-size:14px;font-weight:600;margin-bottom:3px">purge 실행</div>
            <div style="font-size:12px;color:var(--text4)">관리자 암호가 필요합니다.</div>
          </div>
          <button class="btn-orange" id="btnPurge" onclick="startPurge()">⚡ 메모리 정리</button>
        </div>
      </div>
    </div>
  </div>

  <div id="detailPages"></div>
</div>
</div>

<div class="toast" id="toast"></div>

<script>
const TARGETS = __TARGETS__;
const TOKEN   = '__TOKEN__';
let sizes = {};
let rawSizes = {};   // 서버 원본 값 (-1 권한 없음, -2 시간 초과 포함)
let cleanLog = {};
let currentPage = 'dashboard';
let _scanLock = false;

// 상태를 바꾸는 API는 POST + 토큰으로만 호출 (실패 시 {error} 반환)
function post(path) {
  return fetch(path, {method: 'POST', headers: {'X-Token': TOKEN}})
    .then(r => r.json())
    .catch(() => ({error: 'network'}));
}

const ERR_MSG = {
  no_permission: '권한이 없습니다.',
  timeout:       '시간이 초과되었습니다.',
  network:       '앱과 연결할 수 없습니다.',
  forbidden:     '요청이 거부되었습니다. 페이지를 새로고침하세요.',
};
function errText(e) { return ERR_MSG[e] || e; }

// ── 내비게이션 ──────────────────────────────────────────────
function goto(key) {
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.page').forEach(el => el.classList.remove('active'));
  const nav = document.getElementById('nav-' + key);
  if (nav) nav.classList.add('active');
  const pg  = document.getElementById('page-' + key);
  if (pg)  pg.classList.add('active');
  currentPage = key;
  if (key === 'memory' && document.getElementById('memBody').style.display === 'none')
    loadMemory();
  updateLastCleaned(key);
}

// ── 대시보드 빌드 ────────────────────────────────────────────
function buildDashboard() {
  const grid = document.getElementById('dashGrid');
  grid.innerHTML = '';
  TARGETS.forEach(t => {
    const d = document.createElement('div');
    d.className = 'dash-tile';
    d.id = 'tile-' + t.key;
    d.onclick = () => goto(t.key);
    d.innerHTML = `<div class="t-icon">${t.icon}</div>
                   <div class="t-label">${t.label}</div>
                   <div class="t-size nil" id="tsz-${t.key}">--</div>`;
    grid.appendChild(d);
  });

  const cl = document.getElementById('dashCheckList');
  cl.innerHTML = '';
  TARGETS.forEach(t => {
    const lbl = document.createElement('label');
    lbl.style.cssText =
      'display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text2);cursor:pointer;padding:4px 0';
    lbl.innerHTML =
      `<input type="checkbox" id="chk-${t.key}" ${t.default === false ? '' : 'checked'}
         style="width:15px;height:15px;accent-color:#007AFF;cursor:pointer"
         onchange="updateSelCount()">
       ${t.icon} ${t.label}
       <span style="color:var(--text4);font-size:12px" id="clsz-${t.key}"></span>`;
    cl.appendChild(lbl);
  });
  updateSelCount();
}

// ── 상세 페이지 빌드 ─────────────────────────────────────────
function buildDetailPages() {
  const container = document.getElementById('detailPages');
  TARGETS.forEach(t => {
    const p = document.createElement('div');
    p.className = 'page';
    p.id = 'page-' + t.key;
    p.innerHTML = `
      <div class="detail-hero">
        <div class="big-icon">${t.icon}</div>
        <div>
          <h2>${t.label}</h2>
          <div class="detail-desc">${t.desc}</div>
          <div class="detail-path">${t.detail}</div>
          <div class="last-cleaned" id="lc-${t.key}"></div>
        </div>
      </div>
      <div class="card" style="padding:20px">
        <div style="font-size:13px;color:var(--text4);margin-bottom:6px">정리 가능한 용량</div>
        <div class="size-display nil" id="dsz-${t.key}">--</div>
        <div id="dperm-${t.key}"></div>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <button class="btn-gray btn-sm" onclick="rescanOne('${t.key}')">🔍 다시 스캔</button>
          <button class="btn-danger btn-sm" id="dclean-${t.key}"
                  onclick="cleanOne('${t.key}')" disabled>🗑 지금 삭제</button>
        </div>
      </div>`;
    container.appendChild(p);
  });
}

// ── 마지막 정리 날짜 표시 ────────────────────────────────────
function updateLastCleaned(key) {
  const el = document.getElementById('lc-' + key);
  if (!el) return;
  const date = cleanLog[key];
  el.textContent = date ? `마지막 정리: ${date}` : '';
}

async function loadCleanLog() {
  try {
    cleanLog = await fetch('/api/cleanlog').then(r => r.json());
  } catch(e) { cleanLog = {}; }
}

// ── 디스크 정보 ──────────────────────────────────────────────
async function loadDiskInfo() {
  try {
    const d = await fetch('/api/diskinfo').then(r => r.json());
    if (!d.total) return;
    document.getElementById('diskbar').style.display = 'flex';
    document.getElementById('diskUsed').textContent  = fmtSize(d.used) + ' 사용';
    document.getElementById('diskFree').textContent  = '여유 ' + fmtSize(d.free);
    const fill = document.getElementById('diskFill');
    fill.style.width = d.pct + '%';
    fill.className = 'diskbar-fill' + (d.pct > 90 ? ' crit' : d.pct > 75 ? ' warn' : '');
  } catch(e) {}
}

// ── 전체 스캔 ────────────────────────────────────────────────
async function startScan() {
  if (_scanLock) return;
  _scanLock = true;
  document.getElementById('btnCleanAll').disabled = true;
  document.getElementById('scanBadge').textContent = '스캔 중...';
  document.getElementById('scanBtn').disabled = true;
  showProg(true, '파일 크기 계산 중...');
  TARGETS.forEach(t => setSize(t.key, null));

  try {
    const r = await fetch('/api/scan');
    if (!r.ok) throw new Error('scan failed');
    const data = await r.json();
    sizes = {};
    let total = 0;
    TARGETS.forEach(t => {
      const sz = data[t.key] ?? 0;
      sizes[t.key] = sz < 0 ? 0 : sz;
      setSize(t.key, sz);
      if (sz > 0) total += sz;
    });
    document.getElementById('scanBadge').textContent = '확보 가능: ' + fmtSize(total);
    document.getElementById('btnCleanAll').disabled = false;
    updateSelCount();
  } catch(e) {
    document.getElementById('scanBadge').textContent = '스캔 실패';
    showToast('⚠️ 스캔 중 오류가 발생했습니다.');
  }
  hideProg();
  document.getElementById('scanBtn').disabled = false;
  _scanLock = false;
  loadDiskInfo();
}

// ── 단일 항목 재스캔 ─────────────────────────────────────────
async function rescanOne(key) {
  setSize(key, null);
  try {
    const r = await fetch('/api/scan_one?key=' + key);
    const data = await r.json();
    const sz = data[key] ?? 0;
    sizes[key] = sz < 0 ? 0 : sz;
    setSize(key, sz);
  } catch(e) {
    setSize(key, 0);
  }
}

// ── 크기 업데이트 ────────────────────────────────────────────
function setSize(key, sz) {
  const clr = sz === null ? 'nil'
            : sz < 0     ? 'err'
            : sz > 500e6 ? 'big'
            : sz > 50e6  ? 'med'
            : sz > 0     ? 'sml' : 'nil';
  const txt = sz === null ? '스캔 중...'
            : sz === -2  ? '시간 초과'
            : sz < 0     ? '권한 없음'
            : fmtSize(sz);
  if (sz !== null) rawSizes[key] = sz;
  // 휴지통은 크기를 못 읽어도 Finder로 비울 수 있음
  const cleanable = sz > 0 || (key === 'trash' && sz !== null && sz < 0);

  const nsz = document.getElementById('nsz-' + key);
  if (nsz) nsz.textContent = sz === null ? '' : sz < 0 ? '⚠' : fmtSize(sz);
  const tsz = document.getElementById('tsz-' + key);
  if (tsz) { tsz.textContent = txt; tsz.className = 't-size ' + clr; }
  const clsz = document.getElementById('clsz-' + key);
  if (clsz) clsz.textContent = (sz !== null && sz >= 0) ? '(' + fmtSize(sz) + ')' : '';
  const dsz = document.getElementById('dsz-' + key);
  if (dsz) { dsz.textContent = txt; dsz.className = 'size-display ' + clr; }
  const dclean = document.getElementById('dclean-' + key);
  if (dclean) dclean.disabled = !cleanable;

  // 휴지통 권한 배너 (권한 없음일 때만)
  const dperm = document.getElementById('dperm-' + key);
  if (dperm && key === 'trash') {
    dperm.innerHTML = sz === -1 ? `
      <div class="trash-perm-box">
        <div class="tpb-title">🔒 macOS 보안 정책으로 접근이 차단됩니다</div>
        <div class="tpb-desc">TCC 정책으로 휴지통은 root·sudo 포함 모든 외부 접근이 차단됩니다.</div>
        <div class="tpb-option">
          <div class="tpb-opt-title">방법 ① 지금 즉시 비우기 (Finder)</div>
          <div class="tpb-opt-desc">독(Dock)의 휴지통 아이콘을 <b>우클릭 → 휴지통 비우기</b></div>
          <button class="btn-primary btn-sm" onclick="openTrashFinder()">🗑 Finder로 열기</button>
        </div>
        <div class="tpb-divider"></div>
        <div class="tpb-option">
          <div class="tpb-opt-title">방법 ② 영구 권한 설정 (권장)</div>
          <div class="tpb-opt-desc">시스템 설정 → 개인 정보 보호 → <b>전체 디스크 접근 권한</b> → Mac Cleaner 켜기</div>
          <button class="btn-orange btn-sm" onclick="openFdaSettings()">⚙️ 시스템 설정 열기</button>
        </div>
      </div>` : '';
  }

  const chk = document.getElementById('chk-' + key);
  if (chk && sz < 0 && !cleanable) { chk.disabled = true; chk.checked = false; }
}

function sizeLabel(key) {
  return (rawSizes[key] ?? 0) < 0 ? '크기 확인 불가' : fmtSize(sizes[key] || 0);
}

// ── 선택 개수 ────────────────────────────────────────────────
function updateSelCount() {
  const sel   = TARGETS.filter(t => document.getElementById('chk-' + t.key)?.checked);
  const total = sel.reduce((s, t) => s + (sizes[t.key] || 0), 0);
  document.getElementById('selCount').textContent = `${sel.length}개 선택  /  ${fmtSize(total)}`;
}

function syncAll() {
  const all = document.getElementById('chkAll').checked;
  TARGETS.forEach(t => {
    const c = document.getElementById('chk-' + t.key);
    if (c && !c.disabled) c.checked = all;
  });
  updateSelCount();
}

// ── 선택 삭제 ────────────────────────────────────────────────
async function cleanSelected() {
  const sel = TARGETS.filter(t => document.getElementById('chk-' + t.key)?.checked);
  if (!sel.length) { showToast('삭제할 항목을 선택하세요.'); return; }

  const totalSz = sel.reduce((s, t) => s + (sizes[t.key] || 0), 0);
  const list    = sel.map(t => `• ${t.icon} ${t.label}  (${sizeLabel(t.key)})`).join('\n');
  if (!confirm(
    `다음 항목을 영구 삭제하시겠습니까?\n\n${list}\n\n합계: ${fmtSize(totalSz)}\n이 작업은 되돌릴 수 없습니다.`
  )) return;

  document.getElementById('btnCleanAll').disabled = true;
  showProg(false, '삭제 중...', 0, sel.length);
  let cleaned = 0;
  const failed = [];
  for (let i = 0; i < sel.length; i++) {
    const t = sel[i];
    document.getElementById('progLabel').textContent = `${t.icon} ${t.label} 삭제 중...`;
    document.getElementById('prog').value = i + 1;
    const d = await post('/api/clean?key=' + t.key);
    if (d.error) failed.push(`${t.label}(${errText(d.error)})`);
    cleaned += d.cleaned || 0;
  }
  hideProg();
  if (failed.length) showToast(`⚠️ ${fmtSize(cleaned)} 확보, 실패: ${failed.join(', ')}`);
  else               showToast(`✅ ${fmtSize(cleaned)} 확보 완료!`);
  await loadCleanLog();
  setTimeout(startScan, 500);
}

// ── 단일 삭제 ────────────────────────────────────────────────
async function cleanOne(key) {
  const t  = TARGETS.find(x => x.key === key);
  if (!confirm(
    `${t.icon} ${t.label} (${sizeLabel(key)}) 을 삭제하시겠습니까?\n이 작업은 되돌릴 수 없습니다.`
  )) return;

  document.getElementById('dclean-' + key).disabled = true;
  showProg(true, `${t.icon} ${t.label} 삭제 중...`);
  const d = await post('/api/clean?key=' + key);
  hideProg();
  if (d.error) {
    showToast('⚠️ ' + errText(d.error));
  } else if (d.unknown) {
    showToast('✅ 휴지통을 비웠습니다.');
    await loadCleanLog();
    updateLastCleaned(key);
  } else {
    showToast(`✅ ${fmtSize(d.cleaned || 0)} 삭제 완료!`);
    await loadCleanLog();
    updateLastCleaned(key);
  }
  await rescanOne(key);
  loadDiskInfo();
}

// ── 휴지통 보조 ──────────────────────────────────────────────
async function openTrashFinder() {
  await post('/api/trash/open_finder');
  showToast('📂 Finder가 열렸습니다. 독의 휴지통 우클릭 → 휴지통 비우기');
}
async function openFdaSettings() {
  await post('/api/trash/open_fda');
  showToast('⚙️ 시스템 설정이 열렸습니다.');
}

// ── 종료 ─────────────────────────────────────────────────────
async function quitApp() {
  if (!confirm('Mac Cleaner를 종료하시겠습니까?')) return;
  await post('/api/quit');
  document.body.innerHTML =
    '<div style="display:flex;align-items:center;justify-content:center;height:100vh;' +
    'font-family:-apple-system,sans-serif;color:var(--text4);font-size:16px">' +
    'Mac Cleaner가 종료되었습니다. 이 탭을 닫으세요.</div>';
}

// ── 메모리 ───────────────────────────────────────────────────
async function loadMemory() {
  try {
    const d = await fetch('/api/memory').then(r => r.json());
    renderMemory(d);
  } catch(e) {
    document.getElementById('memLoading').textContent = '메모리 정보를 불러올 수 없습니다.';
  }
}
function renderMemory(d) {
  document.getElementById('memLoading').style.display = 'none';
  document.getElementById('memBody').style.display    = 'block';
  const total = d.total || 1;
  document.getElementById('barUsed').style.width     = (d.used    /total*100).toFixed(1)+'%';
  document.getElementById('barInactive').style.width = (d.inactive/total*100).toFixed(1)+'%';
  document.getElementById('barFree').style.width     = (d.free    /total*100).toFixed(1)+'%';
  document.getElementById('mUsed').textContent  = fmtSize(d.used);
  document.getElementById('mInact').textContent = fmtSize(d.inactive);
  document.getElementById('mFree').textContent  = fmtSize(d.free);
}
async function startPurge() {
  const btn = document.getElementById('btnPurge');
  btn.disabled = true; btn.textContent = '정리 중...';
  showToast('🔐 관리자 암호 입력창을 확인하세요...');
  const d = await post('/api/purge');
  btn.disabled = false; btn.textContent = '⚡ 메모리 정리';
  if      (d.error === 'cancelled') showToast('❌ 취소되었습니다.');
  else if (d.error)                 showToast('오류: ' + errText(d.error));
  else { showToast(`✅ ${fmtSize(d.freed||0)} 메모리 확보!`); if(d.after) renderMemory(d.after); }
}

// ── 유틸 ─────────────────────────────────────────────────────
function fmtSize(n) {
  if (n <= 0) return '0 B';
  const u = ['B','KB','MB','GB','TB']; let i = 0;
  while (n >= 1024 && i < u.length-1) { n /= 1024; i++; }
  return n.toFixed(1) + ' ' + u[i];
}
function showProg(indeterminate, label, val=0, max=100) {
  const p = document.getElementById('prog');
  document.getElementById('progLabel').textContent = label;
  if (indeterminate) { p.removeAttribute('value'); p.max = 100; }
  else { p.max = max; p.value = val; }
  document.getElementById('progWrap').style.display = 'block';
}
function hideProg() {
  document.getElementById('prog').value = 0;
  document.getElementById('progWrap').style.display = 'none';
}
let _toastTimer;
function showToast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg; el.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), 3200);
}

// ── 초기화 ───────────────────────────────────────────────────
buildDashboard();
buildDetailPages();
Promise.all([loadCleanLog(), loadDiskInfo()]).then(() => startScan());

// ── heartbeat ────────────────────────────────────────────────
// 탭이 닫히면 ping이 끊겨 서버 watchdog이 종료함 (새로고침에는 종료되지 않음)
const ping = () => fetch('/api/ping').catch(() => {});
setInterval(ping, 3000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) ping(); });
</script>
</body>
</html>"""


# ─── 진입점 ───────────────────────────────────────────────────
if __name__ == "__main__":
    PORT = _find_port(PORT)

    if _try_wake_existing():
        safe_print("기존 인스턴스에 reopen 신호 전송 → 종료")
        sys.exit(0)

    threading.Thread(target=_ctrl_server,  daemon=True).start()
    threading.Thread(target=start_server,  daemon=True).start()

    _server_ready.wait(timeout=5)

    if _server_error:
        err = _server_error.replace("\\", "\\\\").replace('"', '\\"')
        msg = f"Mac Cleaner를 시작할 수 없습니다.\\n\\n오류: {err}"
        subprocess.run([BIN["osascript"], "-e",
                        f'display alert "Mac Cleaner 오류" message "{msg}"'],
                       capture_output=True)
        sys.exit(1)

    threading.Thread(target=_watchdog, daemon=True).start()

    url = f"http://127.0.0.1:{PORT}"
    safe_print(f"Mac Cleaner 실행 중: {url}")
    safe_print("종료: Ctrl+C 또는 브라우저 ✕ 버튼")

    webbrowser.open(url)

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        safe_print("\n종료합니다.")
        os._exit(0)
