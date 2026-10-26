"""
crawler_with_login.py
---------------------
Phiên bản tự động login + crawl cho QLDT Phenikaa.
Được gọi từ server.py qua API endpoint /api/crawl_auto.

Cú pháp:
    python crawler_with_login.py profile               (dùng Chrome/Edge đã đăng nhập)
    python crawler_with_login.py <username> <password> (tự login bằng MSSV/Password)
    python crawler_with_login.py --mode profile|manual|remote|cdp|password ...
    Thêm --headless để chạy Chrome ẩn (không hiện cửa sổ).
"""

import json
import shutil
import sys
import tempfile
import re
import time
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import build_roadmap

START_URL = "https://qldtbeta.phenikaa-uni.edu.vn/congsinhvien/login.aspx"
# Dùng riêng cho chế độ "profile" (đã có sẵn session/cookie): vào thẳng
# trang dashboard thay vì đi qua login.aspx, để tránh phụ thuộc vào việc
# login.aspx có tự redirect người đã đăng nhập hay không.
DASHBOARD_URL = "https://qldtbeta.phenikaa-uni.edu.vn/congsinhvien/index.aspx#dashboard"
QLDT_HOST = "qldtbeta.phenikaa-uni.edu.vn"
# Trang chủ marketing (phenikaa-uni.edu.vn) KHÔNG phải QLĐT — nếu Chrome đứng
# ở đây thì phải chuyển ngay về cổng sinh viên.
DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "capture_all"


def _is_qldt_url(url: str) -> bool:
    u = (url or "").lower()
    return QLDT_HOST in u


def ensure_qldt_page(page, prefer_dashboard: bool = True, retries: int = 2):
    """Ép tab đang mở phải ở domain QLĐT (qldtbeta), không phải web trường.

    Trả về True nếu sau cùng URL thuộc qldtbeta; False nếu vẫn lệch domain.
    """
    target = DASHBOARD_URL if prefer_dashboard else START_URL
    for attempt in range(max(1, retries)):
        try:
            current = page.url or ""
        except Exception:
            current = ""
        if _is_qldt_url(current):
            return True
        if current and not _is_qldt_url(current):
            emit(
                "login", "info",
                f"Tab đang ở trang không phải QLĐT ({current[:80]}). "
                f"Đang chuyển sang Cổng sinh viên...",
                12,
            )
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            emit("login", "info", f"Không mở được {target}: {e}")
            # Thử login.aspx nếu dashboard lỗi
            if prefer_dashboard:
                try:
                    page.goto(START_URL, wait_until="domcontentloaded", timeout=30000)
                except Exception as e2:
                    emit("login", "error", f"Không mở được trang QLĐT: {e2}")
                    return False
        try:
            page.wait_for_timeout(400)
        except Exception:
            pass
        try:
            if _is_qldt_url(page.url or ""):
                return True
        except Exception:
            pass
    return False

# Bật bằng `--all`: crawl TOÀN BỘ mục menu (không chỉ 3 mục chính) để khảo sát dữ liệu.
CRAWL_ALL = False
# Không bao giờ bấm vào các mục này khi crawl toàn bộ (tránh tự đăng xuất).
SKIP_MENU_WORDS = ("đăng xuất", "dang xuat", "logout", "thoát", "đổi mật khẩu")

ONLY_HASHES = {
    "#diemhoc",
    "#chuontrinhhoc",
    "#tracuu",
}

# Mỗi mục menu tương ứng 1 bảng <table id="..."> cụ thể mà build_roadmap.py
# cần đọc (xem load_curriculum/load_transcript trong build_roadmap.py).
# Dùng để CHỜ ĐÚNG bảng đó xuất hiện thay vì đoán "HTML ngừng đổi" bằng độ
# dài innerHTML — cách đoán cũ có thể báo "ổn định" quá sớm (đặc biệt sau khi
# initMain() được gọi qua setTimeout(...,0), DOM có thể chưa kịp bắt đầu đổi
# tại thời điểm đo) và làm crawl lưu nhầm trang cũ. "#tracuu" không có table
# cố định (là danh sách dạng thẻ/card) nên để None -> dùng lại cách đo cũ.
HASH_TABLE_ID = {
    "#chuontrinhhoc": "tblHocPhan",
    "#diemhoc": "tblTongHopDiemHP",
    "#tracuu": None,
}

# Thời gian chờ tối đa (ms) cho dòng dữ liệu thật xuất hiện, RIÊNG theo từng
# trang — vì "Tra cứu điểm" (#diemhoc) là trang backend QLDT xử lý NẶNG NHẤT
# (query cả bảng điểm), đã quan sát thấy có lúc mất >10s mới trả dữ liệu,
# trong khi "Chương trình học" (#chuontrinhhoc) thường nhanh hơn nhiều. Nếu
# dùng chung 1 mốc thấp, trang điểm dễ bị cắt ngang trước khi kịp load xong.
HASH_TIMEOUT_MS = {
    "#chuontrinhhoc": 10000,
    "#diemhoc": 20000,
}


# ============ Helpers ============

def wait_for_stable_content(page, timeout_ms: int = 6000, poll_ms: int = 250, stable_polls: int = 2):
    """
    Đợi tới khi độ dài HTML của trang NGỪNG thay đổi trong `stable_polls` lần
    đo liên tiếp, thay vì đợi `networkidle` (không bao giờ đạt sớm vì QLDT
    liên tục có request nền theo dõi hoạt động) hoặc sleep cố định (luôn ăn
    đủ thời gian dù trang đã load xong từ lâu).

    Trả về sớm ngay khi nội dung ổn định -> nhanh hơn nhiều so với chờ cố
    định, nhưng vẫn có `timeout_ms` làm giới hạn an toàn nếu trang không bao
    giờ "đứng yên" (ví dụ có đồng hồ/animation chạy liên tục).
    """
    deadline = time.time() + (timeout_ms / 1000)
    last_len = -1
    stable_count = 0

    while time.time() < deadline:
        try:
            cur_len = page.evaluate("() => document.body ? document.body.innerHTML.length : 0")
        except Exception:
            cur_len = -1

        if cur_len == last_len and cur_len > 0:
            stable_count += 1
            if stable_count >= stable_polls:
                return
        else:
            stable_count = 0
        last_len = cur_len
        page.wait_for_timeout(poll_ms)
    # Hết timeout mà vẫn chưa ổn định -> chấp nhận trạng thái hiện tại, không
    # chờ thêm nữa (trang có thể vẫn đủ dữ liệu để crawl).


def emit(step: str, status: str, message: str, progress: int = 0):
    obj = {
        "step": step,
        "status": status,
        "message": message,
        "progress": progress,
        "ts": time.time(),
    }
    print(f"LOG:{json.dumps(obj, ensure_ascii=False)}", flush=True)


# ============ Browser Profile (Chrome/Edge đã đăng nhập sẵn) ============

def find_browser_executable():
    """Tìm đường dẫn Chrome hoặc Edge (ưu tiên Chrome)."""
    candidates = [
        # Linux / Docker / VPS
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        # Windows local
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def find_user_data_dir():
    """Tìm thư mục User Data của Chrome hoặc Edge (ưu tiên Chrome)."""
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data"),
        os.path.expandvars(r"%USERPROFILE%\AppData\Local\Google\Chrome\User Data"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data"),
        os.path.expandvars(r"%USERPROFILE%\AppData\Local\Microsoft\Edge\User Data"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


QLDT_COOKIE_DOMAIN = "phenikaa-uni.edu.vn"


def _cookie_db_paths(profile_dir: Path):
    """
    File Cookies (SQLite) có thể nằm ở 1 trong 2 chỗ tùy phiên bản Chrome:
    gốc thư mục profile (bản cũ) hoặc trong Network/ (bản mới, ~2020+).
    """
    candidates = [profile_dir / "Cookies", profile_dir / "Network" / "Cookies"]
    return [c for c in candidates if c.exists()]


def _profile_qldt_cookie_access_time(profile_dir: Path) -> int:
    """
    Đọc TRỰC TIẾP file Cookies (SQLite) của 1 profile ở chế độ CHỈ ĐỌC
    (mode=ro&immutable=1) để không cần đóng Chrome/Edge và không bị lỗi
    "database is locked" dù trình duyệt đang chạy.

    Trả về last_access_utc của cookie QLDT gần nhất nếu profile này CÓ đăng
    nhập QLDT, ngược lại trả về 0 (không có cookie nào cho domain QLDT).
    """
    import sqlite3

    best = 0
    for db_path in _cookie_db_paths(profile_dir):
        try:
            uri = f"file:{db_path.as_posix()}?mode=ro&immutable=1"
            conn = sqlite3.connect(uri, uri=True, timeout=1)
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT MAX(last_access_utc) FROM cookies WHERE host_key LIKE ?",
                    (f"%{QLDT_COOKIE_DOMAIN}%",),
                )
                row = cur.fetchone()
                if row and row[0]:
                    best = max(best, row[0])
            finally:
                conn.close()
        except Exception:
            continue  # DB lỗi/không đọc được -> coi như profile này không có cookie
    return best


def find_default_profile(user_data_dir):
    """
    Tìm đúng profile Chrome/Edge ĐANG CÓ SESSION ĐĂNG NHẬP QLDT, thay vì
    đoán qua "profile dùng gần nhất".

    Lịch sử các lần sửa:
    1) Bản đầu: lấy BỪA profile đầu tiên liệt kê được trong thư mục ->
       thường sai vì thứ tự đó không liên quan gì tới việc đăng nhập.
    2) Bản sửa trước: dùng "profile.last_used" trong file "Local State" ->
       đúng hơn nhưng vẫn có thể sai nếu người dùng có NHIỀU profile Chrome
       mở song song và profile "dùng gần nhất" lại là profile KHÔNG đăng
       nhập QLDT (ví dụ vừa lướt web ở Profile 3 nhưng QLDT lại đăng nhập
       ở Profile 1).
    3) Bản này: quét TẤT CẢ profile, đọc trực tiếp file Cookies (SQLite,
       chỉ đọc) của từng profile để tìm profile nào THỰC SỰ có cookie cho
       domain QLDT, và chọn profile có cookie được truy cập GẦN NHẤT trong
       số đó. Đây là tín hiệu đáng tin cậy nhất, không cần đoán.
    """
    if not user_data_dir:
        return "Default"

    base = Path(user_data_dir)
    profile_dirs = [
        entry for entry in base.iterdir()
        if entry.is_dir() and (entry.name == "Default" or entry.name.startswith("Profile "))
    ]

    best_profile = None
    best_access = 0
    for entry in profile_dirs:
        access = _profile_qldt_cookie_access_time(entry)
        if access > best_access:
            best_access = access
            best_profile = entry.name

    if best_profile:
        return best_profile

    # Không profile nào có cookie QLDT -> fallback về "last_used" (Local
    # State), rồi "Default", giống logic cũ, để vẫn cố thử điều gì đó thay
    # vì báo lỗi ngay.
    local_state_path = base / "Local State"
    if local_state_path.exists():
        try:
            with open(local_state_path, encoding="utf-8") as f:
                state = json.load(f)
            last_used = state.get("profile", {}).get("last_used")
            if last_used and (base / last_used).exists():
                return last_used
        except Exception:
            pass

    if (base / "Default").exists():
        return "Default"
    for entry in profile_dirs:
        return entry.name
    return "Default"


def copy_profile_to_temp(user_data_dir: str, profile_name: str) -> str:
    """
    Copy Chrome/Edge profile ra thư mục TEMP để dùng mà không cần đóng Chrome/Edge.
    Chỉ copy các file/thư mục CẦN THIẾT cho cookies + login state:
      - Cookies, Login Data, Preferences, Secure Preferences, Network Cookies
    Bỏ qua Cache, Code Cache, Storage (quá lớn, không cần thiết cho auth).

    Returns: đường dẫn tới thư mục profile đã copy (parent, có Default/ bên trong).
    """
    src_profile = Path(user_data_dir) / profile_name
    if not src_profile.exists():
        raise FileNotFoundError(f"Profile không tồn tại: {src_profile}")

    # Tạo thư mục tạm
    temp_root = Path(tempfile.gettempdir()) / "phenikaa_profile"
    temp_root.mkdir(parents=True, exist_ok=True)

    timestamp = int(time.time())
    dst_profile = temp_root / f"profile_{timestamp}"
    dst_profile.mkdir(parents=True, exist_ok=True)
    (dst_profile / "Default").mkdir(parents=True, exist_ok=True)

    # Copy Local State ở parent (nếu có)
    local_state = Path(user_data_dir) / "Local State"
    if local_state.exists():
        try:
            shutil.copy2(local_state, dst_profile / "Local State")
        except Exception:
            pass

    # File cần copy
    items_to_copy = [
        "Cookies",
        "Cookies-journal",
        "Cookies-wal",
        "Cookies-shm",
        "Login Data",
        "Login Data-journal",
        "Preferences",
        "Secure Preferences",
        "Web Data",
        "Web Data-journal",
    ]
    # Thư mục con cần copy
    # QLDT là 1 SPA (menu render bằng JS edu.system.startApp()) - rất có
    # thể phần xác thực không chỉ dựa vào cookie mà còn dùng token lưu
    # trong Local Storage / IndexedDB của trình duyệt. Trước đây chỉ copy
    # Cookies nên nếu app đọc token từ đây, bản copy sẽ "có cookie nhưng
    # vẫn coi như chưa đăng nhập" - đúng như triệu chứng đang gặp.
    dirs_to_copy = [
        "Network",
        "Extensions",
        "Local Storage",
        "IndexedDB",
        "Session Storage",
    ]

    dst_default = dst_profile / "Default"
    files_copied = []
    for item in items_to_copy:
        src = src_profile / item
        dst = dst_default / item
        if src.exists():
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                files_copied.append(item)
            except (PermissionError, OSError):
                continue

    for d in dirs_to_copy:
        src = src_profile / d
        dst = dst_default / d
        if src.exists():
            try:
                shutil.copytree(src, dst, dirs_exist_ok=True)
                files_copied.append(f"{d}/")
            except (PermissionError, OSError):
                continue

    # Trả về đường dẫn parent (để Playwright launch_persistent_context tự thêm Default/)
    return str(dst_profile)


# ============ QLDT helpers ============

def has_menu(page):
    """Kiểm tra menu sidebar đã render xong."""
    try:
        # Menu được render bất đồng bộ vào #sidebar-menu bởi edu.system.startApp()
        # Cần có ít nhất 1 .sidebar-menu-item bên trong #sidebar-menu
        # (KHÔNG tính ".refeshtrangchu" - đó là mục "Trang chủ" mặc định nằm ngoài #sidebar-menu)
        items = page.evaluate("""() => {
            const container = document.querySelector('#sidebar-menu');
            if (!container) return 0;
            const all = container.querySelectorAll('.sidebar-menu-item');
            // Bỏ qua item "Trang chủ" nếu lỡ nằm trong container
            return all.length;
        }""")
        if items and items >= 1:
            return True
        # Fallback: có link hash dạng #diemhoc hoặc chứa initMain
        if page.query_selector("a[href^='#diemhoc']"):
            return True
        if page.query_selector("[onclick*='initMain']"):
            return True
        # Fallback: URL có chứa #diemhoc hoặc #chuontrinhhoc
        url = page.url
        if "#diemhoc" in url or "#chuontrinhhoc" in url:
            return True
        return False
    except Exception:
        return False


def accept_stay_signed_in(page) -> bool:
    """Tự bấm ``Có`` (Yes) ở màn Microsoft "Duy trì đăng nhập?" (KMSI).

    Bấm "Có" để Microsoft nhớ phiên -> những lần crawl sau trong cùng profile
    Chrome không phải đăng nhập lại. Đồng thời tick "Không hiển thị lại thông
    báo này" (nếu có) để màn này không hiện nữa.

    Chỉ bấm khi CHẮC CHẮN đang ở màn KMSI (marker ``#KmsiDescription`` hoặc
    tiêu đề KMSI rõ ràng). Nút Yes của Microsoft là ``#idSIButton9`` — id này
    cũng dùng cho nút "Next/Sign in" ở các bước khác nên TUYỆT ĐỐI không bấm
    nếu chưa xác nhận đúng màn KMSI.
    """
    try:
        url = (page.url or "").lower()
        if "microsoftonline.com" not in url and "microsoft.com" not in url:
            return False

        is_kmsi = False

        # Marker ổn định và đặc trưng của màn "Stay signed in?".
        kmsi = page.query_selector("#KmsiDescription")
        if kmsi and kmsi.is_visible():
            is_kmsi = True

        # Fallback theo nội dung nếu Microsoft thay DOM.
        if not is_kmsi:
            try:
                title = page.locator(".text-title, [role='heading'], h1").all_inner_texts()
                joined = " ".join(title).lower()
                kmsi_phrases = (
                    "stay signed in",
                    "keep me signed in",
                    "duy trì đăng nhập",
                    "giữ trạng thái đăng nhập",
                )
                is_kmsi = any(x in joined for x in kmsi_phrases)
            except Exception:
                pass

        if not is_kmsi:
            return False

        # Tick "Không hiển thị lại thông báo này" (Don't show this again).
        try:
            cb = page.query_selector("#KmsiCheckboxField")
            if cb and cb.is_visible() and not cb.is_checked():
                cb.check(timeout=1500)
        except Exception:
            pass

        # Nút "Có" / "Yes".
        yes_btn = page.query_selector("#idSIButton9")
        if yes_btn and yes_btn.is_visible():
            yes_btn.click()
            return True

        # Fallback theo nhãn nút nếu id đổi.
        for label in ("Có", "Yes"):
            try:
                btn = page.get_by_role("button", name=label, exact=True).first
                if btn and btn.is_visible():
                    btn.click()
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


# Giữ tên cũ để code/test nào còn gọi vẫn chạy (nhưng giờ bấm "Có").
dismiss_stay_signed_in = accept_stay_signed_in


def _handle_kmsi_on_any_page(context, preferred=None):
    """Tìm màn KMSI trên bất kỳ tab nào của context và bấm Có.
    Trả về page đã bấm (hoặc None)."""
    for candidate in _all_live_pages(context, preferred):
        if accept_stay_signed_in(candidate):
            emit("login", "info", "Đã tự động bấm 'Có' ở màn 'Duy trì đăng nhập?'.", 30)
            try:
                wait_for_stable_content(candidate, timeout_ms=3500)
            except Exception:
                pass
            return candidate
    return None


def select_chuong_trinh_if_needed(page) -> bool:
    """
    Nhiều trang báo cáo (Chương trình học, Điểm...) có dropdown "Chọn chương
    trình" (#dropSearch_ChuongTrinh) mà BẢNG DỮ LIỆU CHỈ TẢI SAU KHI dropdown
    này có giá trị (thường chỉ có đúng 1 lựa chọn thật vì sinh viên chỉ học 1
    chương trình). Trang tự động chọn sẵn dropdown này ở một thời điểm KHÔNG
    CỐ ĐỊNH (một script nền), nên đôi khi script crawl lưu HTML trước khi
    trang kịp tự chọn -> bảng (vd tblHocPhan) bị rỗng dù đã điều hướng đúng
    trang (đã xảy ra thực tế, xác nhận qua file HTML lưu về có <tbody></tbody>
    trống ngay dưới dropdown đang value="").
    Sửa: chủ động tự chọn option đầu tiên có giá trị + tự bắn sự kiện
    'change' để kích hoạt đúng handler mà trang đang lắng nghe (kể cả khi
    handler được gắn qua jQuery, vì jQuery vẫn nhận native change event).
    Trả về True nếu vừa tự chọn (để caller biết mà chờ thêm 1 chút).
    """
    try:
        return bool(page.evaluate("""
            () => {
                const el = document.getElementById('dropSearch_ChuongTrinh');
                if (!el) return false;
                if (el.value) return false;  // đã có giá trị -> không cần làm gì
                const opt = Array.from(el.options).find(o => o.value);
                if (!opt) return false;
                el.value = opt.value;
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            }
        """))
    except Exception:
        return False


# ---- Tự bấm "Sign in using Microsoft" ở trang login.aspx của QLĐT ----------
# Throttle để nếu SSO lỗi và quay lại login.aspx thì không bấm liên tục.
_MS_CLICK_MIN_INTERVAL = 8.0   # giây giữa 2 lần bấm
_MS_CLICK_MAX_TIMES = 4        # tối đa số lần bấm trong một phiên đăng nhập
_ms_click_state = {"last": 0.0, "count": 0}


def reset_microsoft_signin_state():
    """Gọi ở đầu mỗi luồng đăng nhập để đếm lại từ đầu."""
    _ms_click_state["last"] = 0.0
    _ms_click_state["count"] = 0


def click_microsoft_signin(page) -> bool:
    """Tự bấm nút ``Sign in using Microsoft`` trên trang đăng nhập QLĐT.

    Chỉ hoạt động khi tab đang ở ``qldtbeta...login.aspx`` và nút hiển thị;
    không đụng tới trang Microsoft hay các trang khác. Có throttle để tránh
    bấm lặp vô hạn khi SSO bị lỗi.
    """
    try:
        url = (page.url or "").lower()
    except Exception:
        return False
    if QLDT_HOST not in url or "login.aspx" not in url:
        return False

    now = time.time()
    if _ms_click_state["count"] >= _MS_CLICK_MAX_TIMES:
        return False
    if now - _ms_click_state["last"] < _MS_CLICK_MIN_INTERVAL:
        return False

    clicked = False

    # 1) Theo selector/nội dung phổ biến.
    selectors = [
        "button:has-text('Sign in using Microsoft')",
        "a:has-text('Sign in using Microsoft')",
        "[role='button']:has-text('Sign in using Microsoft')",
        "input[type='button'][value*='Microsoft' i]",
        "input[type='submit'][value*='Microsoft' i]",
        "a[href*='microsoftonline' i]",
        "a[href*='microsoft' i]",
        "[id*='microsoft' i]",
        "[class*='microsoft' i]",
    ]
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                clicked = True
                break
        except Exception:
            continue

    # 2) Fallback: tìm theo chữ, rồi click phần tử clickable gần nhất.
    if not clicked:
        try:
            text_el = page.get_by_text(
                re.compile(r"sign in using microsoft", re.I)
            ).first
            if text_el and text_el.is_visible():
                text_el.evaluate("""
                    (el) => {
                        const c = el.closest('a,button,[onclick],[role="button"]') || el;
                        c.click();
                    }
                """)
                clicked = True
        except Exception:
            pass

    if clicked:
        _ms_click_state["last"] = now
        _ms_click_state["count"] += 1
        emit("login", "info",
             "Đã tự động bấm 'Sign in using Microsoft'.", 18)
    return clicked


# ---- Tự điền MSSV/email ở trang đăng nhập Microsoft ------------------------
# Cấu hình bằng biến môi trường (KHÔNG bao giờ tự điền mật khẩu):
#   PHENIKAA_QLDT_EMAIL = mssv@st.phenikaa-uni.edu.vn   (email đầy đủ), hoặc
#   PHENIKAA_QLDT_MSSV  = mssv  (+ PHENIKAA_QLDT_EMAIL_DOMAIN, mặc định
#                                 st.phenikaa-uni.edu.vn)
# Mật khẩu (nếu có) được lấy khỏi environ NGAY khi import để Chrome/Playwright
# (tiến trình con) không thừa hưởng nó. Chỉ giữ trong biến module, không log.
_CRED = {"password": os.environ.pop("PHENIKAA_QLDT_PASSWORD", "")}
# Các host có form mật khẩu: Microsoft, rồi chuyển sang ADFS của trường.
_PASSWORD_HOSTS = ("login.microsoftonline.com", "sso.phenikaa-uni.edu.vn")
_MS_PASS_MAX_TIMES = 1   # chỉ thử 1 lần để tránh khóa tài khoản do sai mật khẩu
_ms_pass_state = {"count": 0, "failed": False}

_MS_USER_MIN_INTERVAL = 5.0
_MS_USER_MAX_TIMES = 2
_ms_user_state = {"last": 0.0, "count": 0}


def _configured_ms_username() -> str:
    email = (os.environ.get("PHENIKAA_QLDT_EMAIL") or "").strip()
    if email:
        return email
    mssv = (os.environ.get("PHENIKAA_QLDT_MSSV") or "").strip()
    if not mssv:
        return ""
    if "@" in mssv:
        return mssv
    domain = (os.environ.get("PHENIKAA_QLDT_EMAIL_DOMAIN")
              or "st.phenikaa-uni.edu.vn").strip().lstrip("@")
    return f"{mssv}@{domain}"


def reset_microsoft_username_state():
    _ms_user_state["last"] = 0.0
    _ms_user_state["count"] = 0


def fill_microsoft_username(page) -> bool:
    """Điền tài khoản vào ô email của login.microsoftonline.com rồi bấm 'Tiếp theo'.

    Chỉ chạy khi đã cấu hình MSSV/email, ô email đang hiển thị và còn trống.
    Không đụng tới ô mật khẩu. Có throttle để không lặp nếu Microsoft báo lỗi.
    """
    username = _configured_ms_username()
    if not username:
        return False
    try:
        url = (page.url or "").lower()
    except Exception:
        return False
    if "login.microsoftonline.com" not in url:
        return False

    now = time.time()
    if _ms_user_state["count"] >= _MS_USER_MAX_TIMES:
        return False
    if now - _ms_user_state["last"] < _MS_USER_MIN_INTERVAL:
        return False

    try:
        box = page.query_selector("input[name='loginfmt'], #i0116, input[type='email']")
        if not box or not box.is_visible():
            return False
        if (box.input_value() or "").strip():
            return False  # đã có chữ (do mình điền hoặc Microsoft nhớ sẵn)
        box.fill(username)
        _ms_user_state["last"] = now
        _ms_user_state["count"] += 1
        # Nút "Tiếp theo" dùng id #idSIButton9 (trùng id nút "Có" ở KMSI, nhưng
        # ở bước này ô email đang hiện nên không nhầm).
        nxt = page.query_selector("#idSIButton9, input[type='submit']")
        if nxt and nxt.is_visible():
            nxt.click()
        else:
            box.press("Enter")
        emit("login", "info", "Đã tự điền tài khoản Microsoft và bấm 'Tiếp theo'. "
             "Hãy nhập mật khẩu trong cửa sổ trình duyệt.", 19)
        return True
    except Exception as e:
        _log_auto_error("điền tài khoản", e)
        return False


def _configured_ms_password() -> str:
    return _CRED.get("password") or ""


def reset_microsoft_password_state():
    _ms_pass_state["count"] = 0
    _ms_pass_state["failed"] = False
    _auth_seen.clear()
    _auth_diagnosed.clear()
    _auto_err_seen.clear()
    _ms_pass_state.pop("fill_fail", None)


_auto_err_seen = set()


def _log_auto_error(where: str, exc: Exception):
    """Ghi lỗi khi tự điền (tối đa 4 dòng khác nhau) thay vì nuốt im lặng."""
    msg = f"{type(exc).__name__}: {str(exc).splitlines()[0][:160] if str(exc) else ''}"
    key = (where, msg)
    if key in _auto_err_seen or len(_auto_err_seen) >= 4:
        return
    _auto_err_seen.add(key)
    emit("login", "info", f"Lỗi khi tự điền ({where}): {msg}", 20)


def _set_password_value(page, box, password: str) -> bool:
    """Nhập mật khẩu vào ô, kiểm tra giá trị thật sự được giữ lại.

    Thử ``fill`` trước; nếu trang xóa/không nhận (JS cần sự kiện bàn phím thật)
    thì gõ từng phím. Không submit ở đây nên thử lại nhiều lần cũng an toàn.
    """
    try:
        box.click(timeout=3000)
    except Exception:
        pass
    box.fill(password)
    if box.input_value() == password:
        return True
    try:
        box.fill("")
        box.focus()
        page.keyboard.type(password, delay=15)
    except Exception as e:
        _log_auto_error("gõ phím", e)
    return box.input_value() == password


def _submit_login_form(page, box) -> bool:
    """Bấm nút đăng nhập (ADFS: #submitButton). Chỉ bấm MỘT lần; nếu thao tác
    chuột lỗi thì thử click bằng JS, cuối cùng mới nhấn Enter."""
    nxt = page.query_selector("#submitButton, #idSIButton9, input[type='submit']")
    if nxt:
        try:
            nxt.click(timeout=3000)
            return True
        except Exception as e:
            _log_auto_error("bấm Sign in", e)
        try:
            clicked = page.evaluate("""() => {
                const el = document.querySelector('#submitButton, #idSIButton9, input[type=submit]');
                if (el) { el.click(); return true; }
                return false;
            }""")
            if clicked:
                return True
        except Exception as e:
            _log_auto_error("click JS", e)
    box.press("Enter")
    return True


def fill_microsoft_password(page) -> bool:
    """Điền mật khẩu rồi bấm 'Sign in' ở trang Microsoft hoặc ADFS của trường.

    Sau bước email, Microsoft chuyển sang ``sso.phenikaa-uni.edu.vn/adfs/ls``
    (ô ``#passwordInput``, nút ``#submitButton``); nếu ô tài khoản ở đó đang
    trống thì điền luôn mã sinh viên.

    Chỉ SUBMIT đúng 1 lần mỗi phiên: nếu báo sai mật khẩu thì dừng và để người
    dùng tự nhập trong cửa sổ Chrome (tránh bị khóa tài khoản do thử lặp).
    Không bao giờ ghi mật khẩu ra log.
    """
    password = _configured_ms_password()
    if not password or _ms_pass_state["failed"]:
        return False
    if _ms_pass_state["count"] >= _MS_PASS_MAX_TIMES:
        return False
    try:
        url = (page.url or "").lower()
    except Exception as e:
        _log_auto_error("đọc URL", e)
        return False
    host = next((h for h in _PASSWORD_HOSTS if h in url), None)
    if not host:
        return False

    if host not in _auth_seen:
        _auth_seen[host] = time.time()
        emit("login", "info", f"Đã tới trang đăng nhập {host}, đang tìm ô mật khẩu...", 19)

    try:
        box = page.query_selector(
            "#passwordInput, input[name='passwd'], #i0118, input[type='password']"
        )
        if not box or not box.is_visible():
            return False
        if (box.input_value() or "").strip():
            return False

        # ADFS: nếu ô tài khoản trống thì điền mã sinh viên trước.
        if "sso.phenikaa-uni.edu.vn" in url:
            username = _configured_ms_username()
            user_box = page.query_selector("#userNameInput")
            if username and user_box and user_box.is_visible() \
                    and not (user_box.input_value() or "").strip():
                user_box.fill(username)

        if not _set_password_value(page, box, password):
            _ms_pass_state["fill_fail"] = _ms_pass_state.get("fill_fail", 0) + 1
            if _ms_pass_state["fill_fail"] in (1, 5):
                emit("login", "info",
                     "Không nhập được mật khẩu vào ô (trang không giữ giá trị). Đang thử lại...", 20)
            return False

        _submit_login_form(page, box)
        _ms_pass_state["count"] += 1
        emit("login", "info", "Đã tự điền mật khẩu và bấm 'Sign in'.", 20)

        # Kiểm tra nhanh xem có báo sai tài khoản/mật khẩu không.
        try:
            page.wait_for_timeout(2500)
            if _login_error_visible(page):
                _ms_pass_state["failed"] = True
                emit("login", "error",
                     "Báo sai tài khoản/mật khẩu. Hãy tự nhập lại trong cửa sổ "
                     "Chrome (app sẽ không tự thử lại để tránh bị khóa tài khoản).", 20)
        except Exception:
            pass
        return True
    except Exception as e:
        _log_auto_error("điền mật khẩu", e)
        return False


def _is_auth_url(url: str) -> bool:
    """URL thuộc luồng SSO (Microsoft hoặc ADFS của trường) -> không được kéo đi."""
    u = (url or "").lower()
    return ("microsoftonline.com" in u or "microsoft.com" in u
            or "sso.phenikaa-uni.edu.vn" in u)


def emit_autofill_status():
    """Báo trong nhật ký việc tự đăng nhập đang BẬT hay TẮT (không lộ mật khẩu)."""
    user = _configured_ms_username()
    if user and _configured_ms_password():
        emit("login", "info",
             f"Tự động đăng nhập: BẬT cho {user} (đã nhận mật khẩu từ form).", 14)
    elif user:
        emit("login", "info",
             f"Tự động điền tài khoản {user}; chưa có mật khẩu nên bạn tự nhập mật khẩu.", 14)
    else:
        emit("login", "info",
             "Tự động đăng nhập: TẮT (chưa nhập mã sinh viên/mật khẩu ở form). "
             "Bạn tự đăng nhập trong cửa sổ Chrome.", 14)


_auth_seen = {}      # host -> thời điểm lần đầu thấy
_auth_diagnosed = set()


def _maybe_diagnose(page):
    """Đang ở trang đăng nhập đã >4s mà vẫn chưa điền được -> ghi 1 lần danh sách
    ô nhập tìm thấy, để chỉnh selector mà không phải đoán mù."""
    if not (_configured_ms_username() and _configured_ms_password()):
        return
    try:
        url = page.url or ""
    except Exception:
        return
    host = next((h for h in _PASSWORD_HOSTS if h in url.lower()), None)
    if not host or host in _auth_diagnosed:
        return
    now = time.time()
    first = _auth_seen.setdefault(host, now)
    if now - first < 4 or _ms_pass_state["count"] >= _MS_PASS_MAX_TIMES:
        return
    _auth_diagnosed.add(host)
    try:
        info = page.evaluate("""() => {
            const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
            return Array.from(document.querySelectorAll('input,button,[id=submitButton]'))
              .slice(0, 25)
              .map(e => `${e.tagName.toLowerCase()}#${e.id || '-'}[name=${e.name || '-'},type=${e.type || '-'},${vis(e) ? 'hiện' : 'ẩn'}]`)
              .join(' | ');
        }""")
    except Exception as e:
        info = f"(không đọc được trang: {e})"
    emit("login", "info",
         f"CHẨN ĐOÁN: đang ở {host} nhưng chưa tự điền được mật khẩu. "
         f"Các ô tìm thấy: {info}", 20)


def _login_error_visible(page) -> bool:
    """Có thông báo lỗi đăng nhập (Microsoft hoặc ADFS) đang hiện không."""
    for sel in ("#passwordError", "#usernameError"):
        el = page.query_selector(sel)
        if el and el.is_visible():
            return True
    # ADFS luôn có #errorText (rỗng khi không lỗi) -> chỉ tính khi có chữ.
    el = page.query_selector("#errorText")
    if el and el.is_visible():
        try:
            return bool((el.inner_text() or "").strip())
        except Exception:
            return False
    return False


def click_congsinhvien(page):
    """Bấm đúng thẻ ``Cổng sinh viên`` ở màn chọn cổng sau SSO.

    Phenikaa có một màn trung gian *ngay trên* ``/congsinhvien/index.aspx#dashboard``
    với hai thẻ "Cổng sinh viên" và "Cổng cán bộ". Vì vậy không thể dùng URL
    ``/congsinhvien/`` để kết luận rằng đã vào app sinh viên. Chỉ bấm khi trang
    thực sự hiển thị đúng nhãn "Cổng sinh viên"; tuyệt đối không bấm trên
    ``login.aspx`` hay các trang Microsoft.
    """
    try:
        url = (page.url or "").lower()
    except Exception:
        url = ""

    # Không tự thao tác trên trang đăng nhập QLDT/Microsoft.
    if (
        "login.aspx" in url
        or "login.microsoftonline.com" in url
        or "microsoft.com" in url
        or "microsoftonline.com" in url
        or "sso.phenikaa-uni.edu.vn" in url
    ):
        return False

    # Các kiểu phần tử thường gặp. Ưu tiên selector có text để tránh bấm nhầm
    # các link khác cũng chứa chuỗi "congsinhvien".
    portal_selectors = [
        "a:has-text('Cổng Sinh Viên')",
        "a:has-text('Cổng sinh viên')",
        "button:has-text('Cổng Sinh Viên')",
        "button:has-text('Cổng sinh viên')",
        "[role='button']:has-text('Cổng Sinh Viên')",
        "[role='button']:has-text('Cổng sinh viên')",
        "[onclick]:has-text('Cổng Sinh Viên')",
        "[onclick]:has-text('Cổng sinh viên')",
    ]
    for selector in portal_selectors:
        try:
            el = page.query_selector(selector)
            if not el or not el.is_visible():
                continue
            el.click()
            return True
        except Exception:
            continue

    # Ở giao diện hiện tại, chữ có thể nằm trong phần tử con của một card có
    # onclick. Tìm đúng text rồi click phần tử clickable gần nhất.
    try:
        text_el = page.get_by_text("Cổng sinh viên", exact=True).first
        if text_el and text_el.is_visible():
            clicked = text_el.evaluate("""
                (el) => {
                    const clickable = el.closest('a,button,[onclick],[role="button"]');
                    if (clickable) {
                        clickable.click();
                        return true;
                    }
                    let p = el.parentElement;
                    for (let i = 0; p && i < 5; i++, p = p.parentElement) {
                        if (p.matches && (p.matches('a,button,[onclick],[role="button"]') ||
                            getComputedStyle(p).cursor === 'pointer')) {
                            p.click();
                            return true;
                        }
                    }
                    el.click();
                    return true;
                }
            """)
            return bool(clicked)
    except Exception:
        pass

    return False

def _qldt_auth_cookie(context):
    """Trả cookie xác thực QLDT nếu callback SSO đã cấp cookie.

    QLDT hiện dùng ``.Auth``. Giữ thêm ``.ASPXAUTH`` làm fallback vì đây là
    tên thường gặp ở ứng dụng ASP.NET cũ. HttpOnly cookie vẫn đọc được qua
    Playwright ``context.cookies()`` nên không cần đụng tới mật khẩu/token.
    """
    try:
        for cookie in context.cookies():
            domain = str(cookie.get("domain") or "").lower()
            name = str(cookie.get("name") or "").lower()
            if "qldtbeta.phenikaa-uni.edu.vn" not in domain:
                continue
            if name in {".auth", ".aspxauth"} and cookie.get("value"):
                return cookie
    except Exception:
        pass
    return None


def _all_live_pages(context, preferred=None):
    """Lấy các page còn sống, ưu tiên popup/tab mở gần nhất.

    Microsoft SSO có thể chạy trong popup/tab mới. Bản cũ chỉ theo dõi page
    ban đầu nên có thể bỏ lỡ trang đã đăng nhập và tiếp tục nhìn trang login.
    """
    pages = []
    try:
        pages = [p for p in context.pages if not p.is_closed()]
    except Exception:
        pass

    # Popup/tab mới thường nằm cuối context.pages -> kiểm tra trước.
    pages = list(reversed(pages))
    if preferred is not None:
        try:
            if not preferred.is_closed():
                pages = [preferred] + [p for p in pages if p is not preferred]
        except Exception:
            pass
    return pages


def _find_authenticated_page(context, preferred=None):
    """Tìm page đã render menu QLDT trong toàn bộ context."""
    for candidate in _all_live_pages(context, preferred):
        if has_menu(candidate):
            return candidate
    return None


def auto_login(page, username: str, password: str):
    """Tự login QLDT bằng username/password."""
    try:
        page.goto(START_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        emit("login", "error", f"Không mở được trang: {e}")
        return False

    page.wait_for_timeout(2000)

    # Tìm input username/password
    user_selectors = [
        "input[name='username']",
        "input[id*='username']",
        "input[name='UserName']",
        "input[name='Username']",
        "input[type='text']",
        "input[placeholder*='Tên']",
        "input[placeholder*='MSSV']",
        "input[placeholder*='tài khoản']",
    ]
    pass_selectors = [
        "input[name='password']",
        "input[id*='password']",
        "input[name='Password']",
        "input[type='password']",
    ]
    btn_selectors = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Đăng nhập')",
        "button:has-text('Login')",
        "a:has-text('Đăng nhập')",
        "button.btn-primary",
    ]

    user_el = None
    for sel in user_selectors:
        user_el = page.query_selector(sel)
        if user_el:
            break
    if not user_el:
        emit("login", "error", "Không tìm thấy ô username.")
        return False

    pass_el = None
    for sel in pass_selectors:
        pass_el = page.query_selector(sel)
        if pass_el:
            break
    if not pass_el:
        emit("login", "error", "Không tìm thấy ô password.")
        return False

    user_el.fill(username)
    pass_el.fill(password)

    btn = None
    for sel in btn_selectors:
        btn = page.query_selector(sel)
        if btn:
            break

    if btn:
        btn.click()
    else:
        pass_el.press("Enter")

    # Đợi menu render (tối đa 30s) - menu được JS tạo async sau
    # edu.system.startApp(). Poll trực tiếp has_menu() ngay từ đầu thay vì
    # chờ cứng networkidle(20s) + sleep(3s) trước -> nếu menu render nhanh
    # (thường 2-5s), ta thoát ngay, không phải cộng thêm 23s vô ích.
    emit("login", "running", "Đang đăng nhập, đợi menu render...", 25)
    deadline = time.time() + 30
    while time.time() < deadline:
        if has_menu(page):
            emit("login", "ok", "Đăng nhập QLDT thành công!", 35)
            return True
        # Tự bấm "Có" nếu Microsoft hiện màn "Stay signed in?" giữa chừng
        if accept_stay_signed_in(page):
            emit("login", "info", "Đã tự động bấm 'Có' ở màn 'Duy trì đăng nhập?'.", 30)
            wait_for_stable_content(page, timeout_ms=5000)
        page.wait_for_timeout(400)

    # Nếu sau 30s vẫn chưa có menu -> có thể session chết hoặc sai pass
    url_after = page.url
    emit("login", "info", f"Vẫn chưa thấy menu sau 30s. URL hiện tại: {url_after}", 28)

    # Thử click "Cổng Sinh Viên" phòng trường hợp redirect về portal
    if click_congsinhvien(page):
        emit("login", "info", "Đã bấm 'Cổng Sinh Viên', đợi menu...", 30)
        # Đợi menu render lại (poll trực tiếp, không chờ networkidle -
        # xem lý do ở lần sửa chính phía trên)
        deadline2 = time.time() + 20
        while time.time() < deadline2:
            if has_menu(page):
                emit("login", "ok", "Vào menu thành công!", 35)
                return True
            page.wait_for_timeout(400)

    emit("login", "error", "Đăng nhập thất bại. Sai username/password?")
    return False


# ============ Menu & Crawl ============

def extract_menu_items(html):
    """Trích xuất các mục menu dạng <a href='#hash'>."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen = set()
    for a in soup.find_all("a"):
        href = a.get("href", "")
        onclick = a.get("onclick", "")
        text = a.get_text(strip=True)
        if href.startswith("#") and len(href) > 1 and text:
            if href not in seen:
                seen.add(href)
                items.append({"hash": href, "text": text, "onclick": onclick})
    return items


def wait_for_table_real_data(page, table_id: str, timeout_ms: int) -> bool:
    """
    Chờ bảng #table_id có DÒNG DỮ LIỆU THẬT (không phải placeholder rỗng).

    Trước đây dùng page.wait_for_selector(f"#{table_id} tr", ...) — cách này
    có thể PASS GIẢ vì một số bảng (đã xác nhận với #tblHocPhan) có sẵn 1 hàng
    <tr> rỗng/placeholder ngay từ lúc trang mới render, TRƯỚC KHI dropdown
    "Chọn chương trình" kịp được chọn và bảng được đổ dữ liệu thật qua AJAX.
    wait_for_selector() thấy <tr> đó là coi như xong ngay, dẫn tới lưu HTML
    với bảng vẫn rỗng dữ liệu thật (chỉ có header) — đúng lỗi đã gặp.

    Hàm này thay vào đó tự poll bằng JS, chỉ coi là "có dữ liệu" khi ít nhất
    1 ô <td> trong <tbody> có nội dung text khác rỗng. Đồng thời, TRONG LÚC
    poll, liên tục gọi lại select_chuong_trinh_if_needed() — vì dropdown có
    thể chưa tồn tại trong DOM tại thời điểm gọi 1 lần duy nhất trước đó
    (trang còn đang render), nên phải thử lại nhiều lần trong suốt thời gian
    chờ thay vì chỉ thử đúng 1 lần lúc bắt đầu.
    """
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        try:
            select_chuong_trinh_if_needed(page)
        except Exception:
            pass
        try:
            has_data = page.evaluate(f"""
                () => {{
                    const t = document.getElementById('{table_id}');
                    if (!t) return false;
                    const rows = t.querySelectorAll('tbody tr');
                    for (const r of rows) {{
                        const cells = r.querySelectorAll('td');
                        for (const c of cells) {{
                            if (c.textContent.trim().length > 0) return true;
                        }}
                    }}
                    return false;
                }}
            """)
        except Exception:
            has_data = False
        if has_data:
            return True
        page.wait_for_timeout(400)
    return False


def prepare_tracuu_search(page, timeout_ms: int = 8000) -> bool:
    """
    Xử lý RIÊNG cho trang "Tra cứu kết quả đăng ký" (#tracuu) — khác hẳn
    trang "Chương trình học": ở đây có 2 dropdown (#dropSearch_HocKy,
    #dropSearch_KeHoach) VÀ 1 NÚT "Xem kết quả đăng ký" (#btnXemKetQuaDangKy)
    phải bấm thì khung #zoneKetQuaDangKy mới được nạp dữ liệu — không có cơ
    chế tự động nào giống #dropSearch_ChuongTrinh ở trang kia. Đã xác nhận
    qua HTML thật: cả 2 dropdown rỗng hoàn toàn (<select ...></select>,
    chưa có <option>) và khung kết quả cũng rỗng ngay cả sau khi chờ lâu —
    vì chưa từng có ai/gì bấm nút Xem.

    Các bước:
      1. Chờ #dropSearch_HocKy có ít nhất 1 <option> (do JS trang tự đổ vào).
      2. Chọn option có value đầu tiên (thường là học kỳ hiện tại/gần nhất),
         bắn 'change' để #dropSearch_KeHoach (phụ thuộc) có cơ hội tự đổ theo.
      3. Chờ 1 chút rồi chọn option đầu tiên của #dropSearch_KeHoach nếu có.
      4. Bấm #btnXemKetQuaDangKy.
    Trả về True nếu bấm được nút Xem (không đảm bảo có dữ liệu — việc đó do
    wait_for_registration_data() ở nơi gọi kiểm tra tiếp).
    """
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        try:
            has_options = page.evaluate("""
                () => {
                    const el = document.getElementById('dropSearch_HocKy');
                    return !!(el && el.options && el.options.length > 0);
                }
            """)
        except Exception:
            has_options = False
        if has_options:
            break
        page.wait_for_timeout(300)
    else:
        return False  # hết giờ mà dropdown vẫn chưa có option nào

    try:
        page.evaluate("""
            () => {
                const el = document.getElementById('dropSearch_HocKy');
                const opt = Array.from(el.options).find(o => o.value);
                if (opt) {
                    el.value = opt.value;
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }
        """)
    except Exception:
        pass

    page.wait_for_timeout(600)  # cho #dropSearch_KeHoach kịp tự đổ theo (nếu phụ thuộc)

    try:
        page.evaluate("""
            () => {
                const el = document.getElementById('dropSearch_KeHoach');
                if (!el) return;
                const opt = Array.from(el.options).find(o => o.value);
                if (opt) {
                    el.value = opt.value;
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }
        """)
    except Exception:
        pass

    page.wait_for_timeout(300)

    try:
        btn = page.query_selector("#btnXemKetQuaDangKy")
        if btn:
            btn.click(timeout=2000)
            return True
    except Exception:
        pass
    return False


_JS_READ_WEEK = """() => {
    const out = [];
    document.querySelectorAll('.calendar-week-table .hour-row').forEach(row => {
        const m = /^h(\\d{2})(\\d{2})(\\d{4})(\\d+)$/.exec(row.id || '');
        row.querySelectorAll('.task.btnLichHoc').forEach(t => {
            const q = (sel) => { const e = t.querySelector(sel); return e ? e.innerText.trim() : ''; };
            const desc = q('.task-description').split('\\n').map(x => x.trim()).filter(Boolean);
            out.push({
                date: m ? `${m[1]}/${m[2]}/${m[3]}` : '',
                title: q('.title'), time: q('.task-date'),
                lop: desc[0] || '', room: desc[1] || '', teacher: desc[2] || '',
            });
        });
    });
    return out;
}"""

_JS_MONTH_WEEKS = """() => {
    const m = {};
    document.querySelectorAll('.calendar-block-cover li.poiter[batdau]').forEach(li => {
        const b = li.getAttribute('batdau');
        if (!m[b]) m[b] = li.getAttribute('title');
    });
    return m;
}"""


def crawl_lichhoc_weeks(page, out_dir: Path, months_back: int = 2, months_forward: int = 3) -> int:
    """Trang Lịch học chỉ vẽ MỘT tuần mỗi lần. Lịch nhỏ bên phải có mỗi ngày là <li class="poiter"
    batdau=... title=...>; bấm vào 1 ngày thì trang nạp tuần đó. Nút chuyển tháng: `.month .prev/.next`.
    Hàm này đi qua nhiều tháng, bấm 1 ngày của mỗi tuần, đọc các ô lịch và ghi lichhoc_weeks.json.
    Trả về số tuần đã đọc."""
    weeks = {}

    def nav(cls):
        try:
            page.evaluate("(c) => { const b = document.querySelector('.month .' + c); if (b) b.click(); }", cls)
        except Exception:
            pass
        page.wait_for_timeout(700)

    def read_month():
        try:
            found = page.evaluate(_JS_MONTH_WEEKS) or {}
        except Exception:
            found = {}
        for batdau, title in found.items():
            if batdau in weeks:
                continue
            page.evaluate(
                "(t) => { const li = Array.from(document.querySelectorAll('.calendar-block-cover li.poiter'))"
                ".find(x => x.getAttribute('title') === t); if (li) li.click(); }", title)
            page.wait_for_timeout(1500)
            weeks[batdau] = page.evaluate(_JS_READ_WEEK)

    for _ in range(months_back):
        nav("prev")
    read_month()
    for _ in range(months_back + months_forward):
        nav("next")
        read_month()

    flat, seen = [], set()
    for batdau, evs in weeks.items():
        for e in evs:
            key = (e["date"], e["time"], e["title"], e["lop"])
            if key not in seen:
                seen.add(key)
                flat.append(e)
    flat.sort(key=lambda e: (e["date"][6:], e["date"][3:5], e["date"][:2], e["time"]))
    (out_dir / "lichhoc_weeks.json").write_text(
        json.dumps({"weeks": weeks, "events": flat}, ensure_ascii=False, indent=2), encoding="utf-8")
    emit("crawl", "info", f"Lịch học: đọc {len(weeks)} tuần, {len(flat)} buổi học.")
    return len(weeks)


def try_generic_view(page) -> bool:
    """Chọn học kỳ đầu tiên có giá trị trong #dropSearch_HocKy rồi bấm nút Xem/Tìm kiếm/Tra cứu
    (nếu có). Dùng cho trang Lịch thi. Không đảm bảo thành công; chỉ cố gắng."""
    try:
        return bool(page.evaluate("""
            () => {
                const el = document.getElementById('dropSearch_HocKy');
                if (el) {
                    const opt = Array.from(el.options).find(o => o.value);
                    if (opt) { el.value = opt.value; el.dispatchEvent(new Event('change', {bubbles: true})); }
                }
                const btns = Array.from(document.querySelectorAll('button, a.btn, input[type=button]'));
                const b = btns.find(x => /^(xem|tìm kiếm|tra cứu)/i.test((x.innerText || x.value || '').trim()) && x.offsetParent !== null);
                if (b) { b.click(); return true; }
                return false;
            }
        """))
    except Exception:
        return False


def wait_for_registration_data(page, timeout_ms: int = 8000) -> str:
    """
    Chờ RIÊNG cho trang "Tra cứu kết quả đăng ký" (#tracuu) — trang này không
    có <table id="..."> cố định như 2 trang kia nên trước đây dùng
    wait_for_stable_content() (đoán qua độ dài HTML ngừng đổi). Cách đó có
    thể PASS GIẢ y hệt lỗi đã gặp ở #tblHocPhan: nếu khung rỗng/loading
    "đứng yên" đủ 2 lần đo liên tiếp, nó bị coi là "ổn định" dù chưa có thẻ
    môn đăng ký nào (.subject-item) render ra.

    Trả về 1 trong: "has_data" (đã thấy >=1 thẻ .subject-item — có đăng ký),
    "empty_confirmed" (khung chứa đã có nội dung khác rỗng nhưng không có
    .subject-item nào — hợp lý là SV không đăng ký môn nào kỳ này),
    "no_zone" (không tìm thấy khung #zoneKetQuaDangKy/#zonemasonrybq — cấu
    trúc trang có thể đã đổi, cần fallback cách đo cũ),
    "timeout" (hết giờ mà khung vẫn hoàn toàn trống — nhiều khả năng chưa
    load xong).
    """
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        try:
            state = page.evaluate("""
                () => {
                    const zone = document.getElementById('zoneKetQuaDangKy')
                              || document.getElementById('zonemasonrybq');
                    if (!zone) return 'no_zone';
                    if (zone.querySelectorAll('.subject-item').length > 0) return 'has_data';
                    if (zone.textContent.trim().length > 0) return 'empty_confirmed';
                    return 'still_empty';
                }
            """)
        except Exception:
            state = "no_zone"
        if state in ("has_data", "empty_confirmed", "no_zone"):
            return state
        page.wait_for_timeout(400)
    return "timeout"


def crawl_pages(context, page, items, out_dir: Path):
    """Click từng menu item, lưu HTML vào out_dir."""
    saved = {}

    # Khi --all: ghi lại các phản hồi XHR/fetch của từng trang vào network_<tên>.json
    # (chỉ URL, tham số gửi đi và nội dung trả về; KHÔNG lưu header/cookie).
    net_buf = []

    def _on_response(resp):
        try:
            req = resp.request
            if req.resource_type not in ("xhr", "fetch"):
                return
            try:
                body = resp.text()
            except Exception:
                body = ""
            net_buf.append({
                "url": resp.url, "method": req.method, "status": resp.status,
                "post_data": (req.post_data or "")[:2000], "body": body[:300000],
            })
        except Exception:
            pass

    if CRAWL_ALL:
        page.on("response", _on_response)
    # Trước đây có sleep cố định 2s ở đây "cho chắc" trước khi bắt đầu crawl
    # trang đầu tiên — không cần thiết vì menu đã render xong (đã qua
    # has_menu() ở bước login) và mỗi trang bên dưới đã tự chờ ổn định qua
    # wait_for_stable_content().

    for it in items:
        h = it["hash"]
        text = it["text"]
        safe_text = "".join(c if c.isalnum() or c in "_-" else "_" for c in text)[:40]
        filename = f"{safe_text}.html"
        out_path = out_dir / filename
        net_buf.clear()

        try:
            table_id = HASH_TABLE_ID.get(h)
            got_data = False

            # QUAN TRỌNG: độ trễ của backend QLDT KHÔNG ổn định giữa các lần
            # chạy — có lần "Chương trình học" nhanh còn "Tra cứu điểm" chậm,
            # có lần lại ngược lại. Một mốc timeout cố định cho từng trang là
            # không đủ tin cậy. Sửa: thử điều hướng + chờ dữ liệu tối đa 2 lần
            # cho mỗi mục; nếu lần 1 hết giờ mà bảng vẫn rỗng, thử điều hướng
            # lại lần 2 (rất có thể lần đầu bị timeout ngay khi backend sắp
            # trả xong, nên thử lại thường sẽ nhanh hơn nhiều so với lần đầu).
            for attempt in range(2):
                navigated = False

                # Cách 1: click menu item (ưu tiên vì giống thao tác thật nhất)
                # QUAN TRỌNG: nếu mục này đang nằm trong 1 nhóm accordion cha CHƯA
                # MỞ (vd "Góc học tập"), phần tử tồn tại trong DOM nhưng KHÔNG
                # "visible" (display:none ở nhóm cha). el.click() mặc định của
                # Playwright sẽ tự chờ tới 30s cho phần tử trở nên visible rồi mới
                # click -> với link nằm trong nhóm chưa mở, nó KHÔNG BAO GIỜ
                # visible nên cứ đứng chờ đủ 30s/mục mới chịu rơi xuống Cách 2 (đã
                # quan sát thấy log đứng yên lâu đúng như vậy). Sửa: tự kiểm tra
                # is_visible() trước (trả về ngay lập tức, không chờ) — nếu không
                # visible thì bỏ qua Cách 1 luôn, đi thẳng Cách 2 (onclick qua
                # evaluate không quan tâm phần tử có visible hay không).
                try:
                    el = page.query_selector(f"a[href='{h}']")
                    if el and el.is_visible():
                        el.click(timeout=2000)  # timeout ngắn phòng hờ, không phải 30s mặc định
                        navigated = True
                except Exception:
                    pass

                # Cách 2: nếu cách 1 không tìm được phần tử, thử gọi onclick
                if not navigated:
                    try:
                        fn = it.get("onclick")
                        if fn and "initMain" in fn:
                            # QUAN TRỌNG: page.evaluate() sẽ tự động AWAIT nếu biểu
                            # thức truyền vào trả về 1 Promise. initMain() rất có
                            # thể là async function gọi API lấy dữ liệu (query
                            # bảng điểm...) -> nếu backend QLDT xử lý chậm,
                            # page.evaluate(fn) có thể bị khóa CỨNG hàng chục giây
                            # (đã quan sát thấy treo ~40s), bất kể chờ dữ liệu bên
                            # dưới có timeout bao lâu, vì code Python còn chưa
                            # chạy tới được dòng đó.
                            #
                            # Sửa: bọc lệnh gọi trong setTimeout(...,0) để nó chạy
                            # ở macrotask riêng. setTimeout() không trả về Promise,
                            # nên page.evaluate() trả quyền điều khiển lại NGAY LẬP
                            # TỨC, còn initMain() vẫn chạy nền bình thường.
                            page.evaluate(f"() => {{ setTimeout(() => {{ {fn} }}, 0); }}")
                            navigated = True
                    except Exception:
                        pass

                # Cách 3: fallback cuối cùng, đổi hash trực tiếp
                if not navigated:
                    try:
                        page.evaluate(f"location.hash = '{h}';")
                    except Exception:
                        pass

                # Chủ động tự chọn dropdown "Chọn chương trình" nếu trang có
                # và đang để trống — xem giải thích chi tiết ở
                # select_chuong_trinh_if_needed(). Gọi 1 lần ở đây cho các
                # trang không có bảng cố định (vd "#tracuu"); với trang có
                # table_id, việc chọn dropdown được lặp lại NGAY TRONG
                # wait_for_table_real_data() bên dưới, vì dropdown có thể
                # chưa tồn tại trong DOM tại đúng thời điểm gọi 1 lần này.
                if select_chuong_trinh_if_needed(page):
                    emit("crawl", "info",
                         f"Đã tự chọn 'Chương trình' cho mục '{text}'.")
                    page.wait_for_timeout(500)

                # Chờ đúng bảng dữ liệu (<table id="...">) mà build_roadmap.py
                # cần đọc thực sự xuất hiện VÀ CÓ DÒNG DỮ LIỆU THẬT bên trong
                # (không chỉ <tr> placeholder rỗng — xem docstring của
                # wait_for_table_real_data để biết lý do phải kiểm tra kỹ vậy).
                if table_id:
                    timeout_ms = HASH_TIMEOUT_MS.get(h, 10000)
                    if wait_for_table_real_data(page, table_id, timeout_ms):
                        got_data = True
                        break  # thành công, không cần thử lại
                    if attempt == 0:
                        emit("crawl", "info",
                             f"Bảng #{table_id} chưa có dữ liệu sau "
                             f"{timeout_ms/1000:.0f}s cho mục '{text}' — "
                             f"thử điều hướng lại lần 2...")
                        continue  # thử lại vòng for (attempt=1)
                    # Đã thử lại nhưng vẫn không có dữ liệu — bảng có thể
                    # thực sự rỗng (SV không có dữ liệu ở mục này), hoặc
                    # điều hướng thất bại cả 2 lần. Ghi log rõ ràng thay
                    # vì âm thầm lưu 1 bảng rỗng.
                    emit("crawl", "info",
                         f"Bảng #{table_id} vẫn không có dữ liệu sau khi "
                         f"thử lại cho mục '{text}' — điều hướng có thể "
                         f"đã thất bại hoặc trang tải quá chậm.")
                    wait_for_stable_content(page, timeout_ms=3000)
                else:
                    # "#tracuu" cần chủ động chọn dropdown + bấm nút "Xem"
                    # trước, KHÁC với #chuontrinhhoc (xem prepare_tracuu_search).
                    if h == "#tracuu":
                        if prepare_tracuu_search(page):
                            emit("crawl", "info",
                                 f"Đã chọn học kỳ + bấm 'Xem' cho mục '{text}'.")
                    state = wait_for_registration_data(page, timeout_ms=8000)
                    if state in ("has_data", "empty_confirmed"):
                        got_data = True
                        break
                    if state == "no_zone":
                        # Cấu trúc trang không như kỳ vọng -> fallback cách cũ
                        wait_for_stable_content(page, timeout_ms=6000)
                        got_data = True
                        break
                    # state == "timeout"
                    if attempt == 0:
                        emit("crawl", "info",
                             f"Chưa thấy dữ liệu đăng ký sau 8s cho mục "
                             f"'{text}' — thử điều hướng lại lần 2...")
                        continue
                    emit("crawl", "info",
                         f"Vẫn chưa thấy dữ liệu đăng ký sau khi thử lại "
                         f"cho mục '{text}'.")
                    wait_for_stable_content(page, timeout_ms=3000)

        except Exception:
            pass

        # Tra cứu điểm: bảng điểm THEO HỌC KỲ (#zone_bangdiem, panel hocky_*)
        # là nguồn duy nhất để đếm môn từng F (Điều 27.7). Trước đây chỉ chờ khi
        # CRAWL_ALL=True nên crawl thường lưu HTML khi zone còn trống → hoc_vu
        # đọc 0 lần học, không báo vượt 5%. Luôn chờ với #diemhoc.
        if h == "#diemhoc":
            try:
                try:
                    page.evaluate(
                        """() => {
                            if (window.jQuery) {
                                var d = window.jQuery('#dropSearch_ChuongTrinh');
                                if (d.length) d.trigger('change');
                            }
                        }"""
                    )
                except Exception:
                    pass
                _filled = """() => {
                    const z = document.getElementById('zone_bangdiem');
                    if (!z) return false;
                    if (z.querySelectorAll('[id^="hocky_"]').length > 0) return true;
                    return (z.innerText || '').trim().length > 80;
                }"""
                try:
                    page.wait_for_function(_filled, timeout=20000)
                    emit("crawl", "info", "Đã có bảng điểm theo học kỳ (#zone_bangdiem).")
                except Exception:
                    try:
                        page.evaluate(
                            """() => {
                                const nodes = Array.from(document.querySelectorAll('a,button,span,li'));
                                const t = nodes.find(n => /bảng điểm/i.test((n.textContent || '').trim()));
                                if (t) t.click();
                            }"""
                        )
                        page.wait_for_function(_filled, timeout=12000)
                        emit("crawl", "info", "Đã có bảng điểm theo học kỳ (sau khi bấm tab).")
                    except Exception:
                        emit(
                            "crawl", "info",
                            "Chưa thấy #zone_bangdiem đầy đủ sau khi chờ — "
                            "cảnh báo học lại 5% có thể thiếu. Nên mở Tra cứu điểm, "
                            "đợi bảng theo từng kỳ hiện rồi crawl lại.",
                        )
                page.wait_for_timeout(500)
            except Exception:
                pass

        # Lịch học / Lịch thi: dữ liệu được JS (lichgiang.js) vẽ SAU khi tải trang, nên chờ thêm.
        if CRAWL_ALL and h in ("#lichhoc", "#lichthi"):
            try:
                if h == "#lichthi":
                    try_generic_view(page)
                page.wait_for_timeout(4000)
                wait_for_stable_content(page, timeout_ms=3000)
            except Exception:
                pass

        if CRAWL_ALL and net_buf:
            try:
                (out_dir / f"network_{safe_text}.json").write_text(
                    json.dumps(list(net_buf), ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        # Lưu HTML
        try:
            content = page.content()
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(content)
            saved[h] = str(out_path)
            emit("crawl", "info",
                 f"Đã lưu {filename}",
                 50 + int(35 * (len(saved) / max(len(items), 1))))
        except Exception as e:
            emit("crawl", "error", f"Lỗi lưu {filename}: {e}")

        # Lịch học chi tiết từng lớp (bấm "Chi tiết" ở Tra cứu kết quả đăng ký) -> lich_hoc_phan.json.
        # Chạy SAU khi đã lưu HTML để việc mở/đóng modal không ảnh hưởng file vừa lưu.
        if h == "#tracuu":
            try:
                from class_schedule import crawl_class_schedules
                crawl_class_schedules(page, out_dir=out_dir, emit=emit)
            except Exception as e:
                emit("crawl", "info", f"Không lấy được lịch học chi tiết từng lớp: {e}")

        if CRAWL_ALL and h == "#lichhoc":
            try:
                crawl_lichhoc_weeks(page, out_dir)
            except Exception as e:
                emit("crawl", "info", f"Không lấy được các tuần của Lịch học: {e}")

    if CRAWL_ALL:
        try:
            page.remove_listener("response", _on_response)
        except Exception:
            pass
    return saved


# ============ Mode 1: Dùng profile Chrome/Edge ============

# ---- Chẩn đoán khi session profile không hoạt động ----

def _looks_like_login_page(page) -> bool:
    """Kiểm tra trang hiện tại có phải là form đăng nhập không (bị đá về
    login dù ta cố vào thẳng dashboard -> session/cookie không được app
    công nhận)."""
    try:
        if page.query_selector("input[type='password']"):
            return True
        if "login.aspx" in (page.url or "").lower():
            return True
    except Exception:
        pass
    return False


def _dump_debug_snapshot(page, tag: str):
    """
    Lưu screenshot + HTML của trang hiện tại ra thư mục tạm để người dùng
    (hoặc dev) xem được THỰC SỰ trang đang hiển thị gì, thay vì đoán mù -
    hữu ích khi debug từ xa (qua log text) không thấy được giao diện.
    Không raise lỗi nếu chụp thất bại (chỉ để debug, không được làm hỏng
    luồng crawl chính).
    """
    try:
        debug_dir = Path(tempfile.gettempdir()) / "phenikaa_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        png_path = debug_dir / f"{tag}_{ts}.png"
        html_path = debug_dir / f"{tag}_{ts}.html"
        try:
            page.screenshot(path=str(png_path), full_page=True)
        except Exception:
            png_path = None
        try:
            html_path.write_text(page.content(), encoding="utf-8")
        except Exception:
            html_path = None
        return png_path, html_path
    except Exception:
        return None, None


def open_with_profile(p, headless: bool = False):
    """
    Chế độ 1: Dùng Chrome/Edge profile đã đăng nhập sẵn.
    COPY profile ra thư mục tạm để không bị xung đột với Chrome/Edge thật.
    """
    browser_path = find_browser_executable()
    user_data_dir = find_user_data_dir()
    profile = find_default_profile(user_data_dir)

    if not browser_path:
        emit("login", "error",
             "Không tìm thấy Chrome/Edge trên máy. Vui lòng cài Chrome hoặc dùng chế độ nhập mật khẩu.")
        return False

    if not user_data_dir:
        emit("login", "error", "Không tìm thấy thư mục User Data của Chrome/Edge.")
        return False

    emit("login", "info", f"Trình duyệt: {Path(browser_path).name}", 5)

    qldt_access = _profile_qldt_cookie_access_time(Path(user_data_dir) / profile)
    if qldt_access:
        emit("login", "info", f"Profile: {profile} (đã tìm thấy cookie đăng nhập QLDT)", 7)
    else:
        emit("login", "info",
             f"Profile: {profile} (KHÔNG tìm thấy cookie QLDT ở profile nào — có thể bạn chưa "
             "từng đăng nhập QLDT trên Chrome/Edge máy này, hoặc cookie đã hết hạn). "
             "Vẫn thử profile này, nhưng nếu lỗi hãy dùng chế độ 'Mở cửa sổ trình duyệt để tự đăng nhập'.",
             7)

    # COPY profile ra thư mục tạm
    emit("login", "running", "Đang copy profile sang thư mục tạm...", 8)
    try:
        copied_user_data_dir = copy_profile_to_temp(user_data_dir, profile)
    except Exception as e:
        emit("login", "error", f"Không copy được profile: {e}")
        return False

    emit("login", "info", "Đã copy profile xong", 15)

    try:
        if headless:
            emit(
                "login", "info",
                "Đang chạy profile ở chế độ headless. "
                "Lưu ý: Microsoft SSO có thể từ chối session headless "
                "(dễ bị bắt đăng nhập lại). Nếu lỗi, hãy bỏ --headless.",
                9,
            )

        context = p.chromium.launch_persistent_context(
            user_data_dir=copied_user_data_dir,
            executable_path=browser_path,
            # Mặc định headless=False vì Microsoft SSO hay phát hiện
            # trình duyệt headless và từ chối cookie/session ngầm.
            # Có thể ép bằng cờ --headless nếu muốn chạy ngầm (rủi ro cao hơn).
            headless=headless,
            viewport={"width": 1366, "height": 768},
            args=[
                # KHÔNG dùng --no-sandbox trên Windows: cờ này chỉ thật sự
                # cần khi chạy Linux/Docker với quyền root. Trên Windows nó
                # không cần thiết, và Chrome sẽ hiện hẳn 1 banner cảnh báo
                # "cờ dòng lệnh không được hỗ trợ" ngay trên đầu trang -
                # đây là dấu hiệu rất dễ bị các trang như Microsoft nhận ra
                # là trình duyệt tự động hóa, phản tác dụng với mục đích
                # "trông giống trình duyệt thật" mà mình đang cố đạt được.
                "--disable-blink-features=AutomationControlled",
                # Tránh Chrome hiện màn hình "First Run"/thiết lập ban đầu
                # (chọn làm trình duyệt mặc định, nhập dữ liệu, v.v.) khi mở
                # 1 thư mục profile mới copy - có thể che mất trang QLDT
                # thật hoặc chiếm tab đầu tiên.
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )

        if context.pages:
            page = context.pages[0]
        else:
            page = context.new_page()

        emit("login", "running", "Đang mở trang dashboard QLDT...", 20)
        if not ensure_qldt_page(page, prefer_dashboard=True, retries=3):
            emit("login", "error", f"Không mở được trang QLĐT ({DASHBOARD_URL}).")
            context.close()
            return False
        emit("login", "info", f"Đã mở QLĐT: {page.url}", 22)

        # Đợi menu render (tối đa 25s) - menu được JS tạo async
        emit("login", "running", "Đợi menu render...", 25)
        reset_microsoft_signin_state()
        reset_microsoft_username_state()
        reset_microsoft_password_state()
        emit_autofill_status()
        deadline = time.time() + 25
        while time.time() < deadline:
            if has_menu(page):
                emit("login", "ok", "Vào menu thành công (đã đăng nhập sẵn)!", 35)
                return context, page
            if (click_microsoft_signin(page) or fill_microsoft_username(page)
                    or fill_microsoft_password(page)):
                wait_for_stable_content(page, timeout_ms=3000)
            else:
                _maybe_diagnose(page)
            if accept_stay_signed_in(page):
                emit("login", "info", "Đã tự động bấm 'Có' ở màn 'Duy trì đăng nhập?'.", 24)
                wait_for_stable_content(page, timeout_ms=3500)
            page.wait_for_timeout(300)

        # Có thể đang ở portal -> click Cổng Sinh Viên
        emit("login", "running", "Chưa thấy menu sau khi chờ đăng nhập; thử tìm nút 'Cổng Sinh Viên'...", 27)
        if click_congsinhvien(page):
            emit("login", "info", "Đã bấm 'Cổng Sinh Viên', đợi menu...", 30)
            # Đợi menu render lại (poll trực tiếp, không chờ networkidle)
            deadline2 = time.time() + 20
            while time.time() < deadline2:
                if has_menu(page):
                    emit("login", "ok", "Vào menu thành công sau khi click Cổng Sinh Viên!", 35)
                    return context, page
                page.wait_for_timeout(300)

        emit("login", "error",
             "Không thấy menu sau khi mở link. Có thể session đã hết hạn. "
             "Hãy mở Chrome/Edge lên, vào QLDT một lần để refresh, rồi thử lại.")

        # Chẩn đoán thêm: bị đá về trang login, hay ở đúng trang nhưng
        # thiếu menu vì lý do khác? Log rõ để không phải đoán mù nữa.
        try:
            current_url = page.url
        except Exception:
            current_url = "(không lấy được URL)"

        if _looks_like_login_page(page):
            emit("login", "error",
                 f"CHẨN ĐOÁN: trang hiện tại là FORM ĐĂNG NHẬP (url: {current_url}) "
                 "-> session/cookie đã copy KHÔNG được server công nhận (bị đá về login "
                 "dù đã vào thẳng dashboard). Không phải do thiếu menu/selector.")
        else:
            emit("login", "error",
                 f"CHẨN ĐOÁN: trang hiện tại KHÔNG phải form đăng nhập (url: {current_url}) "
                 "nhưng vẫn không thấy menu -> có thể trang đã vào đúng nhưng menu render "
                 "chậm hơn 25s, hoặc cấu trúc HTML khác với lúc code được viết.")

        png_path, html_path = _dump_debug_snapshot(page, "profile_login_fail")
        if png_path:
            emit("login", "info", f"Đã lưu ảnh chụp màn hình để debug: {png_path}")
        if html_path:
            emit("login", "info", f"Đã lưu HTML trang để debug: {html_path}")

        context.close()
        return False

    except Exception as e:
        err_msg = str(e)
        emit("login", "error", f"Lỗi mở browser: {err_msg}")
        return False


# ============ Mode CDP: Mở tab mới NGAY TRONG Chrome đang chạy ============
# Khác với Mode 1 (copy toàn bộ profile ra thư mục TẠM rồi mở một CỬA SỔ
# CHROME MỚI, tách biệt hoàn toàn với Chrome thật đang mở): mode này dùng
# Chrome DevTools Protocol (CDP) để "gắn" (attach) thẳng vào đúng tiến
# trình Chrome người dùng đang dùng, rồi mở THÊM MỘT TAB MỚI trong đó —
# y hệt như người dùng tự tay bấm Ctrl+T rồi gõ địa chỉ web. Tab này dùng
# chung session/cookie thật đang đăng nhập, không cần copy gì cả.
#
# YÊU CẦU BẮT BUỘC: Chrome phải được khởi động kèm cờ
#   --remote-debugging-port=<PORT>   (mặc định PORT=9222)
# TRƯỚC KHI bấm nút này. Nếu Chrome đang chạy mà KHÔNG có cờ này, không thể
# gắn được (Chrome không tự bật cổng debug khi đang chạy) — phải đóng HẾT
# cửa sổ Chrome rồi mở lại bằng lệnh/shortcut có kèm cờ trên.

CDP_DEFAULT_PORT = 9223
# Profile riêng của Advisor: không đụng vào Chrome người dùng đang sử dụng.
CDP_PROFILE_DIR = Path(__file__).resolve().parent / ".qldt_chrome_profile"


def _cdp_endpoint(port: int = CDP_DEFAULT_PORT) -> str:
    return f"http://localhost:{port}"


def _acquire_single_cdp_page(context):
    """Đảm bảo chỉ còn ĐÚNG 1 tab để crawl trong Chrome riêng của Advisor.

    - Ưu tiên tái sử dụng tab QLĐT đang mở (giữ nguyên đăng nhập/trạng thái).
    - Nếu không có thì dùng tab trống/tab đầu tiên; chỉ new_page() khi Chrome
      chưa có tab nào.
    - Đóng các tab QLĐT / tab trống THỪA (Chrome này là profile riêng của
      Advisor nên đóng tab thừa là an toàn; không đụng tab khác của người dùng).
    """
    try:
        pages = [pg for pg in context.pages if not pg.is_closed()]
    except Exception:
        pages = []

    def _url(pg):
        try:
            return (pg.url or "").lower()
        except Exception:
            return ""

    keep = next((pg for pg in pages if _is_qldt_url(_url(pg))), None)
    if keep is None:
        keep = next((pg for pg in pages if _url(pg) in ("", "about:blank", "chrome://newtab/")), None)
    if keep is None and pages:
        keep = pages[0]
    if keep is None:
        return context.new_page()

    for pg in pages:
        if pg is keep:
            continue
        u = _url(pg)
        if _is_qldt_url(u) or u in ("", "about:blank", "chrome://newtab/"):
            try:
                pg.close()
            except Exception:
                pass
    return keep


def _close_cdp_page_safely(context, page):
    """Crawl xong ở mode CDP: nếu đây là tab DUY NHẤT thì giữ lại (đóng tab cuối
    sẽ tắt luôn cửa sổ Chrome riêng, lần sau phải mở lại); chỉ đóng khi còn tab khác."""
    try:
        others = [pg for pg in context.pages if pg is not page and not pg.is_closed()]
        if others:
            page.close()
    except Exception:
        pass


def open_with_cdp(p, port: int = CDP_DEFAULT_PORT):
    """
    Chế độ CDP: KHÔNG copy profile, KHÔNG mở cửa sổ Chrome mới.
    Gắn (attach) vào chính Chrome thật đang chạy (đã bật sẵn
    --remote-debugging-port), rồi mở MỘT TAB MỚI trong đúng cửa sổ đó để
    crawl. Khi xong, CHỈ đóng tab này lại — KHÔNG được đóng browser/context
    vì đó là cửa sổ Chrome thật của người dùng (đóng context sẽ đóng luôn
    mọi tab khác họ đang mở, rất nguy hiểm).
    """
    endpoint = _cdp_endpoint(port)
    emit("login", "running", f"Đang chuẩn bị Chrome riêng cho Advisor ({endpoint})...", 5)

    # Nếu Chrome chuyên dụng chưa chạy, tự mở nó với profile riêng và cổng
    # CDP chỉ bind localhost. Không đóng hoặc điều khiển Chrome cá nhân khác.
    try:
        with urllib.request.urlopen(endpoint + "/json/version", timeout=1.5) as response:
            cdp_ready = response.status == 200
    except Exception:
        cdp_ready = False

    if not cdp_ready:
        browser_path = find_browser_executable()
        if not browser_path:
            emit("login", "error", "Không tìm thấy Chrome/Edge. Hãy cài Google Chrome rồi thử lại.")
            return False
        try:
            CDP_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            command = [
                browser_path,
                f"--user-data-dir={CDP_PROFILE_DIR}",
                f"--remote-debugging-port={port}",
                "--remote-debugging-address=127.0.0.1",
                "--no-first-run",
                "--no-default-browser-check",
                # KHÔNG truyền DASHBOARD_URL ở đây: nếu Chrome tự mở 1 tab QLĐT
                # rồi code lại new_page() thêm 1 tab nữa thì sẽ có 2 tab QLĐT.
                # Tab duy nhất sẽ được điều hướng bởi _acquire_single_cdp_page().
                "about:blank",
            ]
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, creationflags=creation_flags)
            emit("login", "info", "Đã mở Chrome riêng. Nếu đây là lần đầu, hãy đăng nhập QLĐT trong cửa sổ vừa mở.", 10)
            deadline = time.time() + 20
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(endpoint + "/json/version", timeout=1) as response:
                        if response.status == 200:
                            cdp_ready = True
                            break
                except Exception:
                    time.sleep(0.4)
            if not cdp_ready:
                emit("login", "error", f"Chrome đã được khởi chạy nhưng CDP chưa sẵn sàng tại {endpoint}. Hãy đóng cửa sổ Chrome riêng của Advisor rồi thử lại.")
                return False
        except Exception as e:
            emit("login", "error", f"Không thể tự mở Chrome riêng: {e}")
            return False

    emit("login", "running", f"Đang kết nối tới Chrome qua CDP ({endpoint})...", 12)
    try:
        browser = p.chromium.connect_over_cdp(endpoint)
    except Exception as e:
        emit("login", "error",
             f"Không kết nối được tới Chrome riêng của Advisor tại {endpoint}: {e}. "
             "Hãy đóng cửa sổ Chrome riêng của Advisor rồi thử lại; Chrome cá nhân không bị ảnh hưởng.")
        return False

    try:
        contexts = browser.contexts
        if not contexts:
            emit("login", "error",
                 "Kết nối CDP thành công nhưng không thấy cửa sổ Chrome nào đang mở. "
                 "Hãy mở ít nhất 1 cửa sổ Chrome rồi thử lại.")
            return False
        context = contexts[0]
    except Exception as e:
        emit("login", "error", f"Lỗi khi lấy cửa sổ Chrome đang mở: {e}")
        return False

    emit("login", "info", "Đã gắn vào Chrome đang chạy, đang dùng lại tab QLĐT (chỉ 1 tab)...", 12)
    try:
        page = _acquire_single_cdp_page(context)
    except Exception as e:
        emit("login", "error", f"Không mở được tab: {e}")
        return False

    emit("login", "running", "Đang mở trang dashboard QLDT...", 20)
    if not ensure_qldt_page(page, prefer_dashboard=True, retries=3):
        emit(
            "login", "error",
            "Không mở được Cổng sinh viên QLĐT trong tab mới "
            f"({DASHBOARD_URL}). Kiểm tra mạng hoặc đăng nhập tay tại qldtbeta.",
        )
        try:
            page.close()
        except Exception:
            pass
        return False
    emit("login", "info", f"Tab QLĐT đang ở: {page.url}", 22)

    emit("login", "running", "Đợi QLĐT tải menu. Nếu đây là lần đầu, hãy đăng nhập trong cửa sổ Chrome vừa mở; hệ thống sẽ tiếp tục tự động sau khi đăng nhập...", 25)
    reset_microsoft_signin_state()
    reset_microsoft_username_state()
    reset_microsoft_password_state()
    emit_autofill_status()
    deadline = time.time() + 180
    last_portal_click = 0.0
    while time.time() < deadline:
        # Menu có thể hiện ở bất kỳ tab nào (SSO đôi khi mở popup).
        authed = _find_authenticated_page(context, page)
        if authed is not None:
            emit("login", "ok", "Vào menu thành công (dùng session Chrome riêng của Advisor)!", 35)
            return context, authed
        # Đang ở login.aspx -> tự bấm "Sign in using Microsoft".
        for cand in _all_live_pages(context, page):
            if click_microsoft_signin(cand):
                page = cand
                break
        acted = False
        for cand in _all_live_pages(context, page):
            if fill_microsoft_username(cand) or fill_microsoft_password(cand):
                page = cand
                acted = True
                break
        if not acted:
            for cand in _all_live_pages(context, page):
                _maybe_diagnose(cand)
        # Tự bấm "Có" ở màn "Duy trì đăng nhập?" sau khi người dùng đăng nhập xong.
        kmsi_page = _handle_kmsi_on_any_page(context, page)
        if kmsi_page is not None:
            page = kmsi_page
            continue
        # Màn chọn cổng (Cổng sinh viên / Cổng cán bộ) sau SSO.
        if time.time() - last_portal_click > 3:
            last_portal_click = time.time()
            for cand in _all_live_pages(context, page):
                if click_congsinhvien(cand):
                    page = cand
                    emit("login", "info", "Đã chọn 'Cổng sinh viên'.", 30)
                    break
        time.sleep(0.4)

    emit("login", "running", "Chưa thấy menu sau khi chờ đăng nhập; thử tìm nút 'Cổng Sinh Viên'...", 27)
    if click_congsinhvien(page):
        emit("login", "info", "Đã bấm 'Cổng Sinh Viên', đợi menu...", 30)
        deadline2 = time.time() + 20
        while time.time() < deadline2:
            if has_menu(page):
                emit("login", "ok", "Vào menu thành công sau khi click Cổng Sinh Viên!", 35)
                return context, page
            page.wait_for_timeout(300)

    emit("login", "error",
         "Không thấy menu trong tab mới. Có thể bạn CHƯA đăng nhập QLDT trong "
         "Chrome riêng của Advisor. Hãy đăng nhập tại cửa sổ Chrome đó rồi bấm 'Cập nhật dữ liệu từ QLĐT' lại.")

    if _looks_like_login_page(page):
        emit("login", "info", "CHẨN ĐOÁN: tab mới đang ở trang đăng nhập QLDT (chưa có session).")

    png_path, html_path = _dump_debug_snapshot(page, "cdp_login_fail")
    if png_path:
        emit("login", "info", f"Đã lưu ảnh chụp màn hình để debug: {png_path}")
    if html_path:
        emit("login", "info", f"Đã lưu HTML trang để debug: {html_path}")

    # Chỉ đóng TAB vừa mở, không đụng tới context/browser thật của người dùng.
    try:
        page.close()
    except Exception:
        pass
    return False


# ============ Mode 2: Tự login bằng MSSV/password ============

def open_with_login(p, username: str, password: str, headless: bool = True):
    """Chế độ 2: Login bằng username/password, không dùng profile."""
    browser_path = find_browser_executable()
    if not browser_path:
        emit("login", "error",
             "Không tìm thấy Chrome/Edge trên máy. Vui lòng cài Chrome.")
        return False

    try:
        # Mặc định headless=True (user không cần thấy cửa sổ).
        # Có thể tắt bằng cách không truyền --headless (hoặc truyền headless=False).
        browser = p.chromium.launch(
            executable_path=browser_path,
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        page = context.new_page()

        if not auto_login(page, username, password):
            browser.close()
            return False

        return context, page

    except Exception as e:
        emit("login", "error", f"Lỗi: {e}")
        return False


def _headed_browser_args():
    """Các cờ ổn định cho Chrome headed ở local và Docker/VPS."""
    args = ["--disable-blink-features=AutomationControlled"]
    if os.environ.get("PHENIKAA_REMOTE_BROWSER") == "1":
        args += [
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
        ]
    return args


# ============ Mode 3: Mở trình duyệt thật, người dùng tự gõ đăng nhập ============
# An toàn nhất cho web app nhiều người dùng: mật khẩu KHÔNG bao giờ đi qua
# server/subprocess này, người dùng gõ trực tiếp vào cửa sổ Chrome thật.

def open_with_manual_login(p, wait_seconds: int = 300, headless: bool = False):
    """Mở Chrome và đợi người dùng tự đăng nhập QLDT.

    Mặc định headless=False vì cần hiện cửa sổ để người dùng gõ đăng nhập.
    Có thể ép headless=True bằng cờ --headless (thường không hữu ích ở mode này).

    Điểm quan trọng của luồng này là KHÔNG giả định SSO luôn quay về đúng
    ``page`` ban đầu. Microsoft/QLDT có thể dùng popup/tab mới; đồng thời có
    trường hợp callback đã cấp ``.Auth`` nhưng vẫn đang đứng ở ``login.aspx``.
    Khi nhận được cookie xác thực, ta chủ động vào ``index.aspx`` đúng MỘT LẦN
    cho mỗi cookie mới thay vì tiếp tục click/reload trang login.
    """
    browser_path = find_browser_executable()

    try:
        browser = p.chromium.launch(
            executable_path=browser_path or None,
            headless=headless,
            args=_headed_browser_args(),
        )
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        page = context.new_page()

        emit("login", "running", "Đang mở cửa sổ trình duyệt + trang QLĐT...", 10)
        if not ensure_qldt_page(page, prefer_dashboard=False, retries=3):
            emit(
                "login", "error",
                "Không mở được Cổng sinh viên QLĐT "
                f"({START_URL}). Không dùng trang chủ phenikaa-uni.edu.vn.",
            )
            browser.close()
            return False
        emit(
            "login", "info",
            f"Đã mở QLĐT: {page.url}",
            12,
        )

        emit("login", "info",
             "Hãy đăng nhập bằng tài khoản QLDT của bạn trong cửa sổ Chrome vừa mở. "
             f"Có {wait_seconds}s để hoàn tất. "
             "Nếu thấy trang chủ trường (phenikaa-uni.edu.vn), đừng đăng nhập ở đó — "
             "đợi app chuyển về qldtbeta hoặc tự dán link Cổng sinh viên.", 15)

        reset_microsoft_signin_state()
        reset_microsoft_username_state()
        reset_microsoft_password_state()
        emit_autofill_status()
        deadline = time.time() + wait_seconds
        last_emit_bucket = None
        last_auth_cookie_value = None
        warned_rejected_cookie = False
        known_page_count = len(_all_live_pages(context, page))

        while time.time() < deadline:
            # 1) Luôn tìm menu trên TẤT CẢ tab/popup, không chỉ page ban đầu.
            authenticated_page = _find_authenticated_page(context, page)
            if authenticated_page is not None:
                emit("login", "ok", "Đăng nhập thành công!", 35)
                return context, authenticated_page

            live_pages = _all_live_pages(context, page)
            if live_pages:
                # Nếu SSO mở popup/tab mới thì theo dõi tab mới nhất.
                if len(live_pages) > known_page_count:
                    emit("login", "info", "Đã phát hiện cửa sổ đăng nhập mới, đang theo dõi phiên SSO...", 18)
                    known_page_count = len(live_pages)
                page = live_pages[0]

            # 1b) Đang ở login.aspx -> tự bấm "Sign in using Microsoft".
            for candidate in live_pages:
                if click_microsoft_signin(candidate):
                    page = candidate
                    try:
                        wait_for_stable_content(candidate, timeout_ms=3000)
                    except Exception:
                        pass
                    break

            # 1c) Đang ở trang đăng nhập Microsoft -> tự điền MSSV/email.
            acted = False
            for candidate in live_pages:
                if fill_microsoft_username(candidate) or fill_microsoft_password(candidate):
                    page = candidate
                    acted = True
                    break
            if not acted:
                for candidate in live_pages:
                    _maybe_diagnose(candidate)

            # 2) Xử lý KMSI trên bất kỳ tab Microsoft nào đang mở.
            dismissed = False
            for candidate in live_pages:
                if accept_stay_signed_in(candidate):
                    dismissed = True
                    page = candidate
                    emit("login", "info", "Đã tự động bấm 'Có' ở màn 'Duy trì đăng nhập?'.", 20)
                    try:
                        wait_for_stable_content(candidate, timeout_ms=3500)
                    except Exception:
                        pass
                    break

            if dismissed:
                authenticated_page = _find_authenticated_page(context, page)
                if authenticated_page is not None:
                    emit("login", "ok", "Đăng nhập thành công!", 35)
                    return context, authenticated_page

            # 3) Callback SSO có thể đã cấp .Auth nhưng UI vẫn đứng ở login.aspx.
            #    Khi cookie mới xuất hiện, chủ động vào dashboard đúng 1 lần.
            auth_cookie = _qldt_auth_cookie(context)
            auth_value = auth_cookie.get("value") if auth_cookie else None
            if auth_value and auth_value != last_auth_cookie_value:
                last_auth_cookie_value = auth_value
                warned_rejected_cookie = False
                emit("login", "info", "QLDT đã cấp phiên đăng nhập, đang mở trang chính...", 25)

                # Ưu tiên một tab QLDT đang sống; nếu tab hiện tại là Microsoft
                # thì dùng tab QLDT khác hoặc tạo tab mới trong cùng context.
                target = None
                for candidate in _all_live_pages(context, page):
                    try:
                        if "qldtbeta.phenikaa-uni.edu.vn" in (candidate.url or "").lower():
                            target = candidate
                            break
                    except Exception:
                        continue
                if target is None:
                    # Dùng lại tab đang theo dõi thay vì mở thêm tab mới
                    # (tránh có 2 tab QLĐT cùng lúc).
                    target = page

                try:
                    target.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=30000)
                    page = target
                    wait_for_stable_content(page, timeout_ms=6000)
                except Exception as e:
                    emit("login", "info", f"Đã có phiên đăng nhập nhưng chưa mở được trang chính: {e}")

                # Sau SSO, Phenikaa hiện thêm màn chọn "Cổng sinh viên" ngay
                # tại index.aspx#dashboard. Bấm thẻ này rồi mới có menu học vụ.
                if click_congsinhvien(page):
                    emit("login", "info", "Đã chọn 'Cổng sinh viên', đang vào hệ thống học vụ...", 30)
                    try:
                        wait_for_stable_content(page, timeout_ms=5000)
                    except Exception:
                        pass

                authenticated_page = _find_authenticated_page(context, page)
                if authenticated_page is not None:
                    emit("login", "ok", "Đăng nhập thành công!", 35)
                    return context, authenticated_page

                # Có cookie mà index.aspx vẫn đá ngược về login -> cookie không
                # được QLDT công nhận. Không goto lặp vô hạn; chờ người dùng SSO
                # lại để nhận một cookie MỚI rồi mới thử lần nữa.
                try:
                    if _looks_like_login_page(page) and not warned_rejected_cookie:
                        emit(
                            "login", "info",
                            "Phiên vừa nhận chưa được QLDT chấp nhận. Hãy bấm đăng nhập Microsoft lại một lần; "
                            "ứng dụng sẽ không tự tải lại trang đăng nhập liên tục nữa.",
                            20,
                        )
                        warned_rejected_cookie = True
                except Exception:
                    pass

            # 4) Nếu đang ở màn chọn cổng sau SSO thì chọn Cổng sinh viên.
            #    Helper chỉ bấm khi thấy đúng nhãn và vẫn chặn login/Microsoft.
            for candidate in _all_live_pages(context, page):
                if click_congsinhvien(candidate):
                    page = candidate
                    try:
                        wait_for_stable_content(page, timeout_ms=3500)
                    except Exception:
                        pass
                    break

            remaining = max(0, int(deadline - time.time()))
            bucket = remaining // 10
            if bucket != last_emit_bucket:
                emit("login", "info", f"Đang đợi bạn đăng nhập... còn {remaining}s", 15)
                last_emit_bucket = bucket
                # Nếu user (hoặc redirect) lạc sang web trường → kéo về QLĐT.
                try:
                    if page and not _is_qldt_url(page.url or "") and not _is_auth_url(page.url or ""):
                        ensure_qldt_page(page, prefer_dashboard=False, retries=1)
                except Exception:
                    pass

            # Dùng sleep thay vì page.wait_for_timeout vì page có thể vừa bị
            # popup SSO đóng; sleep không phụ thuộc page nào còn sống.
            time.sleep(0.75)

        emit("login", "error", "Hết thời gian chờ đăng nhập. Hãy thử lại.")
        browser.close()
        return False

    except Exception as e:
        emit("login", "error", f"Lỗi mở browser: {e}")
        return False


# ============ Main ============

def parse_args():
    """
    Cú pháp mới (dùng cho server.py, hỗ trợ nhiều user):
        python crawler_with_login.py --mode profile --outdir <path>
        python crawler_with_login.py --mode manual  --outdir <path>
        python crawler_with_login.py --mode remote  --outdir <path>
        python crawler_with_login.py --mode cdp     --outdir <path> [--cdp-port 9222]
        python crawler_with_login.py --mode password --username U --password P [--headless]

    Cờ thêm:
        --headless          Chạy Chrome ẩn (không hiện cửa sổ). Mặc định:
                            - password: True (đã ẩn sẵn)
                            - profile / manual / remote: False
                            Lưu ý: mode profile + headless dễ bị Microsoft SSO chặn.

    Cú pháp cũ vẫn còn (CHỈ dùng cá nhân/CLI trên máy riêng, KHÔNG dùng qua
    web server vì phải gõ mật khẩu qua tham số dòng lệnh - server nhiều
    người dùng KHÔNG bao giờ được thu thập mật khẩu QLDT của người khác):
        python crawler_with_login.py profile
        python crawler_with_login.py <mã_sinh_viên_hoặc_email> <password>

    Tiện lợi: với mode "password", nếu username KHÔNG chứa "@" (ví dụ chỉ
    gõ mã sinh viên "23010334"), sẽ tự động thêm đuôi
    "@st.phenikaa-uni.edu.vn" để đăng nhập tài khoản Microsoft của trường.
    """
    global CRAWL_ALL
    args = sys.argv[1:]
    if "--all" in args:
        CRAWL_ALL = True
        args = [a for a in args if a != "--all"]

    # --headless có thể đứng ở bất kỳ đâu
    headless_flag = "--headless" in args
    if headless_flag:
        args = [a for a in args if a != "--headless"]

    mode = "profile"
    outdir = None
    username = None
    password = None
    cdp_port = CDP_DEFAULT_PORT

    if "--mode" in args or "--outdir" in args:
        i = 0
        while i < len(args):
            if args[i] == "--mode" and i + 1 < len(args):
                mode = args[i + 1].lower()
                i += 2
            elif args[i] == "--outdir" and i + 1 < len(args):
                outdir = args[i + 1]
                i += 2
            elif args[i] == "--username" and i + 1 < len(args):
                username = args[i + 1]
                i += 2
            elif args[i] == "--password" and i + 1 < len(args):
                password = args[i + 1]
                i += 2
            elif args[i] == "--cdp-port" and i + 1 < len(args):
                try:
                    cdp_port = int(args[i + 1])
                except ValueError:
                    pass
                i += 2
            else:
                i += 1
    elif len(args) >= 1 and args[0].lower() == "profile":
        mode = "profile"
    elif len(args) >= 2:
        mode = "password"
        username = args[0]
        password = args[1]
    else:
        print(json.dumps({
            "step": "init", "status": "error",
            "message": "Thiếu argument. Cú pháp: crawler_with_login.py "
                       "--mode profile|manual|remote|cdp|password --outdir <path> "
                       "[--username U --password P] [--cdp-port PORT] [--headless]",
            "progress": 0,
        }, ensure_ascii=False), flush=True)
        sys.exit(1)

    # Mặc định theo mode (giữ hành vi cũ nếu không truyền --headless)
    if headless_flag:
        headless = True
    else:
        # password mặc định ẩn, các mode khác mặc định hiện cửa sổ
        headless = (mode == "password")

    return mode, outdir, username, password, cdp_port, headless


def main():
    mode, outdir, username, password, cdp_port, headless = parse_args()

    out_dir = Path(outdir) if outdir else DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    roadmap_path = out_dir / "roadmap.json"

    if mode == "password" and (not username or not password):
        emit("init", "error", "Chế độ password cần --username và --password.")
        sys.exit(1)

    if mode == "password" and username and "@" not in username:
        # Tiện lợi cho dùng CLI cá nhân: cho phép gõ tắt mã sinh viên
        # (VD: 23010334) thay vì phải gõ email đầy đủ mỗi lần. Chỉ tự
        # thêm đuôi khi username KHÔNG chứa "@" - nếu người dùng đã gõ
        # email đầy đủ (kể cả 1 domain khác) thì giữ nguyên, không đoán đè.
        username = f"{username}@st.phenikaa-uni.edu.vn"
        emit("init", "info", f"Đã tự thêm đuôi email: {username}")

    if headless:
        emit("init", "info", "Chế độ headless: Chrome sẽ chạy ẩn (không hiện cửa sổ).", 0)
    else:
        emit("init", "info", "Chế độ headed: Chrome sẽ hiện cửa sổ.", 0)

    emit("init", "running", "Khởi động trình duyệt...", 0)

    saved_paths = None

    # QUAN TRỌNG: toàn bộ phần thao tác với `context`/`page` (lấy menu,
    # crawl_pages, đóng tab/context) PHẢI nằm trong khối `with
    # sync_playwright() as p:` này. Trước đây khối `with` chỉ bao quanh
    # bước đăng nhập — vừa thoát khối `with` là Playwright tự gọi
    # p.stop(), tắt luôn driver/event loop nội bộ, nên mọi lệnh gọi
    # page/context sau đó (page.content(), crawl_pages(), ...) đều gặp lỗi
    # "Event loop is closed! Is Playwright already stopped?". Giữ mọi thao
    # tác Playwright bên trong khối `with` để driver còn sống suốt lúc crawl.
    with sync_playwright() as p:
        if mode == "profile":
            result = open_with_profile(p, headless=headless)

        elif mode == "cdp":
            result = open_with_cdp(p, port=cdp_port)

        elif mode in ("manual", "remote"):
            # remote dùng cùng luồng manual-login, nhưng Chrome được hiển thị
            # trên Xvfb của VPS và stream về web qua VNC/WebSocket.
            result = open_with_manual_login(p, headless=headless)

        elif mode == "password":
            result = open_with_login(p, username, password, headless=headless)

        else:
            emit("init", "error", f"Mode không hợp lệ: {mode}")
            sys.exit(1)

        if not result:
            sys.exit(1)

        context, page = result
        try:
            # Lấy menu items — has_menu() đã xác nhận menu render xong ngay
            # trước khi các hàm login trả về. Vẫn chờ ổn định thêm 1 chút
            # (nhanh, thường <1s) để chắc chắn các item còn lại (nếu có)
            # render xong hết, thay vì sleep cố định 1.5s như trước.
            emit("menu", "running", "Đang lấy danh sách menu...", 45)
            wait_for_stable_content(page, timeout_ms=1500, poll_ms=150)
            html = page.content()
            items = extract_menu_items(html)
            all_items = items
            if CRAWL_ALL:
                items = [it for it in items
                         if not any(w in it["text"].lower() for w in SKIP_MENU_WORDS)]
                # Lưu danh sách menu đầy đủ để biết cổng có những mục nào.
                try:
                    (out_dir / "menu_all.json").write_text(
                        json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
                emit("menu", "info", f"Chế độ --all: sẽ crawl {len(items)}/{len(all_items)} mục menu.", 48)
            else:
                items = [it for it in items if it["hash"] in ONLY_HASHES]

            if not items:
                emit("menu", "error", "Không tìm thấy menu cần thiết. Có thể session đã hết hạn.")
                context.close()
                sys.exit(1)

            emit("menu", "ok", f"Tìm thấy {len(items)} mục cần crawl", 50)

            # Xóa các file HTML cũ trước khi crawl (optional)
            for old in out_dir.glob("*.html"):
                try:
                    old.unlink()
                except Exception:
                    pass

            saved_paths = crawl_pages(context, page, items, out_dir)
            if mode == "cdp":
                # QUAN TRỌNG: context ở mode này LÀ cửa sổ Chrome thật của
                # người dùng (gắn qua CDP) — context.close() sẽ đóng luôn
                # MỌI tab khác họ đang mở. Chỉ đóng đúng tab vừa dùng để crawl.
                _close_cdp_page_safely(context, page)
            else:
                context.close()
        except Exception as e:
            emit("crawl", "error", f"Lỗi crawl: {e}")
            try:
                if mode == "cdp":
                    _close_cdp_page_safely(context, page)
                else:
                    context.close()
            except Exception:
                pass
            sys.exit(1)

    # Phân tích dữ liệu — thực hiện SAU khi khối `with sync_playwright()` đã
    # thoát. Ổn vì đoạn này chỉ đọc lại các file HTML đã lưu trên đĩa
    # (saved_paths), không còn đụng tới bất kỳ đối tượng Playwright nào.
    emit("analyze", "running", "Đang phân tích dữ liệu...", 90)
    try:
        ct_path = saved_paths.get("#chuontrinhhoc")
        diem_path = saved_paths.get("#diemhoc")
        kt_path = saved_paths.get("#tracuu")

        if ct_path and diem_path:
            from build_roadmap import build_roadmap as build_func
            # Xóa roadmap.json cũ TRƯỚC khi phân tích — nếu phân tích thất
            # bại (vd: bảng #tblHocPhan rỗng do dropdown chưa được chọn lúc
            # crawl), không để lại file rỗng từ lần trước "đánh lừa" dashboard
            # hiển thị trạng thái "có dữ liệu nhưng không có gì". Khi đó
            # load_roadmap() trả None -> UI hiển thị emptyState đúng nghĩa.
            if roadmap_path.exists():
                try:
                    roadmap_path.unlink()
                except Exception:
                    pass
            data = build_func(ct_path, diem_path, kt_path)
            roadmap_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            emit("analyze", "ok", f"Phân tích hoàn tất! Đã lưu {roadmap_path.name}", 100)
            sys.exit(0)
        else:
            emit("analyze", "error", "Thiếu HTML cần thiết")
            sys.exit(1)
    except Exception as e:
        emit("analyze", "error", f"Lỗi phân tích: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
