"""
Flask API Server - Backend cho Phenikaa Advisor (tách dữ liệu theo từng trình duyệt/session)
Chạy: python server.py
Mở: http://localhost:5000

Không có form tài khoản riêng của Phenikaa Advisor. Mỗi trình duyệt được cấp một
client_id ngẫu nhiên trong Flask session cookie; toàn bộ HTML crawl + roadmap.json
của trình duyệt đó được lưu riêng tại data/users/<client_id>/.

Khi chạy local, mode "cdp" tự mở/kết nối Chrome riêng của Advisor với profile lưu lâu dài.
Khi deploy VPS với PHENIKAA_REMOTE_BROWSER=1, mode "remote" tạo một desktop
Xvfb + Chrome + VNC riêng cho từng job và stream màn hình về giao diện web.
Người dùng có thể nhập mã sinh viên + mật khẩu ở form crawl để Advisor tự điền
vào trang đăng nhập Microsoft (mã sinh viên tự thêm @st.phenikaa-uni.edu.vn).
Mật khẩu chỉ nằm trong bộ nhớ trong lúc crawl: không lưu đĩa, không ghi log,
không đưa vào job/dòng lệnh. Nếu bỏ trống thì đăng nhập tay như trước, trực
tiếp trong phiên Chrome. Nên chạy qua HTTPS khi deploy (xem DEPLOY_VPS.md).

Lưu ý: đây là tách dữ liệu theo browser session, chưa phải hệ thống tài khoản
đồng bộ đa thiết bị. Hai người dùng cùng một profile trình duyệt sẽ dùng chung
session Advisor; hai trình duyệt/profile khác nhau sẽ có data riêng.
"""

import json
import os
import subprocess
import sys
import threading
import time
import uuid
import secrets
import shutil
import socket
from datetime import timedelta
from pathlib import Path

from flask import Flask, jsonify, send_from_directory, request, Response, session
from flask_cors import CORS

try:
    from flask_sock import Sock
except ImportError:
    Sock = None

from build_roadmap import _sanitize_for_json, make_current_filter  # noqa: F401
from planner import PACE_OPTIONS  # noqa: F401
import advisor
import academic_simulator
import graduation_check
from class_schedule import build_ics

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_PATH = Path(BASE_DIR)


def _load_or_create_secret_key() -> str:
    """Giữ Flask session ổn định qua các lần restart server.

    Production nên truyền PHENIKAA_SECRET_KEY bằng biến môi trường. Nếu chưa
    có, dev/local sẽ tự tạo một key riêng trong .phenikaa_secret_key.
    """
    env_key = os.environ.get("PHENIKAA_SECRET_KEY")
    if env_key:
        return env_key

    secret_file = BASE_PATH / ".phenikaa_secret_key"
    if secret_file.exists():
        key = secret_file.read_text(encoding="utf-8").strip()
        if key:
            return key

    key = secrets.token_hex(32)
    try:
        secret_file.write_text(key, encoding="utf-8")
        try:
            os.chmod(secret_file, 0o600)
        except OSError:
            pass
    except OSError:
        # Hosting read-only vẫn khởi động được, nhưng session sẽ đổi sau restart.
        # Production nên set PHENIKAA_SECRET_KEY để tránh trường hợp này.
        print("[WARN] Không ghi được .phenikaa_secret_key; đang dùng key tạm thời.")
    return key


app = Flask(__name__, static_folder="frontend", static_url_path="")
app.secret_key = _load_or_create_secret_key()
app.config.update(
    SESSION_COOKIE_NAME="phenikaa_advisor_sid",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("PHENIKAA_COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=180),
)
_cors_origins = [x.strip() for x in os.environ.get("PHENIKAA_CORS_ORIGINS", "").split(",") if x.strip()]
if _cors_origins:
    CORS(app, resources={r"/api/*": {"origins": _cors_origins}})
# Same-origin requests do not require permissive CORS. Set PHENIKAA_CORS_ORIGINS
# explicitly only when a separate trusted frontend origin is used.
sock = Sock(app) if Sock is not None else None

NOVNC_DIR = Path(os.environ.get("PHENIKAA_NOVNC_DIR", "/usr/share/novnc"))
REMOTE_BROWSER_REQUESTED = os.environ.get("PHENIKAA_REMOTE_BROWSER", "0") == "1"

def _remote_dependencies_available() -> bool:
    return (
        os.name != "nt"
        and sock is not None
        and NOVNC_DIR.exists()
        and all(shutil.which(x) for x in ("Xvfb", "x11vnc", "fluxbox"))
    )

REMOTE_BROWSER_ENABLED = REMOTE_BROWSER_REQUESTED and _remote_dependencies_available()

# Mỗi client/browser session có thư mục riêng.
DATA_ROOT = BASE_PATH / "data" / "users"
DATA_ROOT.mkdir(parents=True, exist_ok=True)
from qldt_credentials import CredentialError, parse_credentials  # noqa: E402

ALLOWED_CRAWL_MODES = {"profile", "manual", "remote", "cdp"}

# job_id -> {logs, status, client_id, data_dir, ...}
_crawl_jobs = {}
_jobs_lock = threading.RLock()
_desktop_lock = threading.Lock()


def get_client_id() -> str:
    """Lấy/tạo ID ẩn danh cho đúng trình duyệt hiện tại.

    ID nằm trong Flask signed-session cookie, không lấy từ request do client tự
    gửi nên user khác không thể đổi URL/body để đọc data của nhau.
    """
    client_id = session.get("client_id")
    valid = (
        isinstance(client_id, str)
        and len(client_id) == 32
        and all(ch in "0123456789abcdef" for ch in client_id)
    )
    if not valid:
        client_id = uuid.uuid4().hex
        session["client_id"] = client_id
    session.permanent = True
    return client_id


def get_user_data_dir(create: bool = True) -> Path:
    data_dir = DATA_ROOT / get_client_id()
    if create:
        data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _get_owned_job(job_id: str):
    """Chỉ trả job nếu job thuộc đúng browser session đang gọi request."""
    with _jobs_lock:
        job = _crawl_jobs.get(job_id)
    if not job or job.get("client_id") != get_client_id():
        return None
    return job


def _purge_old_jobs(max_age_seconds: int = 6 * 60 * 60):
    """Dọn metadata job cũ trong RAM; không xóa dữ liệu sinh viên trên đĩa."""
    now = time.time()
    with _jobs_lock:
        stale = [
            job_id for job_id, job in _crawl_jobs.items()
            if job.get("status") in ("done", "error")
            and now - job.get("finished_at", job.get("created_at", now)) > max_age_seconds
        ]
        for job_id in stale:
            _crawl_jobs.pop(job_id, None)



# ============ Remote Chrome desktop (VPS) ============

def _find_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _allocate_display() -> int:
    """Chọn display X11 chưa dùng. App production chạy 1 gunicorn worker nhiều threads."""
    for display_num in range(90, 220):
        if not Path(f"/tmp/.X11-unix/X{display_num}").exists():
            return display_num
    raise RuntimeError("Không còn X display trống cho phiên Chrome mới")


def _wait_tcp_port(port: int, timeout: float = 8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("VNC không khởi động kịp thời")


def _terminate_process(proc):
    if not proc:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception:
        pass


def _start_remote_desktop(job: dict):
    """Tạo Xvfb + fluxbox + x11vnc riêng cho đúng crawl job."""
    if not REMOTE_BROWSER_ENABLED:
        raise RuntimeError(
            "Remote browser chưa được bật/thiếu Xvfb, x11vnc, fluxbox, noVNC hoặc flask-sock."
        )

    # Giữ lock đến khi Xvfb/x11vnc bind xong để 2 request đồng thời không
    # chọn trùng DISPLAY hoặc cổng VNC.
    with _desktop_lock:
        display_num = _allocate_display()
        vnc_port = _find_free_local_port()
        display = f":{display_num}"
        env = os.environ.copy()
        env["DISPLAY"] = display
        env["PHENIKAA_REMOTE_BROWSER"] = "1"

        null = subprocess.DEVNULL
        xvfb = subprocess.Popen(
            ["Xvfb", display, "-screen", "0", "1440x900x24", "-nolisten", "tcp", "-ac"],
            stdout=null, stderr=null, env=env,
        )

        # Chờ socket X11 xuất hiện trước khi chạy window manager/VNC.
        x_socket = Path(f"/tmp/.X11-unix/X{display_num}")
        deadline = time.time() + 5
        while time.time() < deadline and not x_socket.exists():
            if xvfb.poll() is not None:
                raise RuntimeError("Xvfb không khởi động được")
            time.sleep(0.05)
        if not x_socket.exists():
            _terminate_process(xvfb)
            raise RuntimeError("Xvfb khởi động quá thời gian")

        fluxbox = subprocess.Popen(["fluxbox"], stdout=null, stderr=null, env=env)
        x11vnc = subprocess.Popen(
            [
                "x11vnc", "-display", display,
                "-rfbport", str(vnc_port),
                "-localhost", "-nopw", "-forever", "-shared",
                "-noxdamage", "-quiet",
            ],
            stdout=null, stderr=null, env=env,
        )
        try:
            _wait_tcp_port(vnc_port)
        except Exception:
            _terminate_process(x11vnc)
            _terminate_process(fluxbox)
            _terminate_process(xvfb)
            raise

        job["display"] = display
        job["vnc_port"] = vnc_port
        job["desktop_processes"] = [x11vnc, fluxbox, xvfb]
        job["crawler_env"] = env


def _stop_remote_desktop(job: dict):
    for proc in job.get("desktop_processes", []):
        _terminate_process(proc)
    job["desktop_processes"] = []


# ============ Serve frontend ============

@app.route("/")
def index():
    return send_from_directory("frontend", "index.html")


@app.route("/api/runtime")
def runtime_config():
    return jsonify({
        "success": True,
        "remote_browser_enabled": REMOTE_BROWSER_ENABLED,
        "remote_browser_requested": REMOTE_BROWSER_REQUESTED,
    })


@app.route("/novnc/<path:filename>")
def novnc_assets(filename):
    if not NOVNC_DIR.exists():
        return "noVNC assets not installed", 404
    return send_from_directory(str(NOVNC_DIR), filename)


# ============ Data helpers ============

def load_roadmap():
    path = get_user_data_dir() / "roadmap.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # Bảo vệ thêm: nếu file cũ (ghi trước khi build_roadmap.py được sửa)
        # còn sót giá trị NaN/Infinity, khử luôn ở đây để không trả JSON hỏng
        # cho trình duyệt (JSON.parse sẽ báo lỗi "Unexpected token 'N'").
        return _sanitize_for_json(data)
    return None


# ============ Data endpoints (tự động tách theo browser session) ============

@app.route("/api/roadmap")
def get_roadmap():
    data = load_roadmap()
    if data:
        updated_at = None
        try:
            updated_at = (get_user_data_dir(create=False) / "roadmap.json").stat().st_mtime
        except OSError:
            pass
        return jsonify({"success": True, "data": data, "updated_at": updated_at})
    return jsonify({"success": False, "error": "Chưa có dữ liệu. Hãy cập nhật dữ liệu từ QLĐT."}), 404


@app.route("/api/class-schedule")
def get_class_schedule():
    """Lịch học chi tiết từng lớp (lich_hoc_phan.json do class_schedule.py crawl)."""
    path = get_user_data_dir(create=False) / "lich_hoc_phan.json"
    if not path.exists():
        return jsonify({
            "success": False,
            "error": "Chưa có lịch học. Hãy cập nhật dữ liệu QLĐT (mục Tra cứu kết quả đăng ký).",
        }), 404
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return jsonify({"success": False, "error": "File lịch học bị lỗi. Hãy cập nhật dữ liệu QLĐT lại."}), 500
    if not isinstance(data, dict) or not isinstance(data.get("lop"), list):
        return jsonify({"success": False, "error": "File lịch học không đúng định dạng."}), 500
    return jsonify({"success": True, "data": _sanitize_for_json(data), "updated_at": path.stat().st_mtime})




def _calendar_token_path(user_dir: Path) -> Path:
    return user_dir / "calendar_share_token.txt"


def _get_or_create_calendar_token(user_dir: Path) -> str:
    """Token bí mật để app lịch (Google/Apple/Outlook) lấy .ics không cần cookie session."""
    token_file = _calendar_token_path(user_dir)
    if token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
        if len(token) >= 32 and all(ch in "0123456789abcdef" for ch in token):
            return token
    token = secrets.token_hex(24)
    user_dir.mkdir(parents=True, exist_ok=True)
    token_file.write_text(token, encoding="utf-8")
    try:
        os.chmod(token_file, 0o600)
    except OSError:
        pass
    return token


def _find_user_dir_by_calendar_token(token: str) -> Path | None:
    if not token or len(token) < 32:
        return None
    if not all(ch in "0123456789abcdef" for ch in token):
        return None
    if not DATA_ROOT.exists():
        return None
    for user_dir in DATA_ROOT.iterdir():
        if not user_dir.is_dir():
            continue
        token_file = _calendar_token_path(user_dir)
        if not token_file.exists():
            continue
        try:
            if token_file.read_text(encoding="utf-8").strip() == token:
                return user_dir
        except OSError:
            continue
    return None


def _load_lich_hoc_phan(user_dir: Path):
    path = user_dir / "lich_hoc_phan.json"
    if not path.exists():
        return None, (jsonify({
            "success": False,
            "error": "Chưa có lịch học. Hãy cập nhật dữ liệu QLĐT (mục Tra cứu kết quả đăng ký).",
        }), 404)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, (jsonify({"success": False, "error": "File lịch học bị lỗi. Hãy cập nhật dữ liệu QLĐT lại."}), 500)
    if not isinstance(data, dict) or not isinstance(data.get("lop"), list):
        return None, (jsonify({"success": False, "error": "File lịch học không đúng định dạng."}), 500)
    return data, None


@app.route("/api/class-schedule/subscribe-url")
def get_class_schedule_subscribe_url():
    """Trả URL đăng ký lịch (webcal + https) để thêm vào app Lịch mà không cần tải file."""
    user_dir = get_user_data_dir(create=True)
    data, err = _load_lich_hoc_phan(user_dir)
    if err:
        return err
    token = _get_or_create_calendar_token(user_dir)
    # Prefer public base URL if set (deploy), else request host
    public_base = (os.environ.get("PHENIKAA_PUBLIC_URL") or "").rstrip("/")
    if public_base:
        https_url = f"{public_base}/api/class-schedule.ics?token={token}"
    else:
        https_url = request.url_root.rstrip("/") + f"/api/class-schedule.ics?token={token}"
    # webcal:// opens Calendar apps on iOS/macOS/Android (many clients)
    if https_url.startswith("https://"):
        webcal_url = "webcal://" + https_url[len("https://"):]
    elif https_url.startswith("http://"):
        webcal_url = "webcal://" + https_url[len("http://"):]
    else:
        webcal_url = https_url
    return jsonify({
        "success": True,
        "https_url": https_url,
        "webcal_url": webcal_url,
        "hint": "Dùng webcal trên iPhone/Mac/Outlook. Google Calendar: Settings → Add calendar → From URL, dán https_url.",
    })


@app.route("/api/class-schedule.ics")
def get_class_schedule_ics():
    """Xuất lịch học ra file iCalendar (.ics).

    - Có cookie session: lấy data của trình duyệt hiện tại (tải file).
    - Có ?token=...: app lịch subscribe được mà không cần cookie (tự cập nhật).
    """
    token = (request.args.get("token") or "").strip()
    if token:
        user_dir = _find_user_dir_by_calendar_token(token)
        if user_dir is None:
            return jsonify({"success": False, "error": "Token không hợp lệ hoặc đã hết hạn."}), 403
        download = False
    else:
        user_dir = get_user_data_dir(create=False)
        download = True

    data, err = _load_lich_hoc_phan(user_dir)
    if err:
        return err

    reminder = request.args.get("reminder", "30")
    try:
        reminder_minutes = int(reminder)
    except (TypeError, ValueError):
        reminder_minutes = 30
    if reminder_minutes < 0:
        reminder_minutes = 0

    ics_body = build_ics(data, reminder_minutes=reminder_minutes)
    headers = {
        # Subscribe clients should re-fetch periodically
        "Cache-Control": "no-cache, max-age=0",
    }
    if download:
        headers["Content-Disposition"] = 'attachment; filename="lich_hoc_phenikaa.ics"'
    else:
        # Inline: calendar apps consume the body directly
        headers["Content-Disposition"] = 'inline; filename="lich_hoc_phenikaa.ics"'
    return Response(
        ics_body,
        mimetype="text/calendar; charset=utf-8",
        headers=headers,
    )

@app.route("/api/summary")
def get_summary():
    data = load_roadmap()
    if not data:
        return jsonify({"success": False, "error": "Chưa có dữ liệu"}), 404

    mon_hoc = data.get("mon_hoc", [])

    status_counts = {}
    for c in mon_hoc:
        stt = c.get("trang_thai", "UNKNOWN")
        status_counts[stt] = status_counts.get(stt, 0) + 1

    tc_da_hoc = data.get("tin_chi_da_hoc", 0)
    tc_toan_khoa = data.get("tin_chi_toan_khoa", 0)
    gpa = data.get("gpa", {})

    completed = [c for c in mon_hoc if c.get("trang_thai") == "HOAN_THANH"]
    co_the_dk = [c for c in mon_hoc if c.get("trang_thai") == "CO_THE_DANG_KY"]
    bi_khoa = [c for c in mon_hoc if c.get("trang_thai") == "BI_KHOA"]
    dang_ky_nay = [c for c in mon_hoc if c.get("trang_thai") == "DANG_KY_KY_NAY"]

    warnings = []
    if status_counts.get("BI_KHOA", 0) > 0:
        warnings.append(f"Có {status_counts['BI_KHOA']} môn đang bị khóa (chờ tiên quyết)")

    warnings.extend(data.get("canh_bao_du_lieu", []) or [])

    return jsonify({
        "success": True,
        "data": {
            "tin_chi_da_hoc": tc_da_hoc,
            "tin_chi_toan_khoa": tc_toan_khoa,
            "gpa": gpa,
            "status_counts": status_counts,
            "warnings": warnings,
            "completed_count": len(completed),
            "co_the_dk_count": len(co_the_dk),
            "bi_khoa_count": len(bi_khoa),
            "dang_ky_nay_count": len(dang_ky_nay),
        },
    })


@app.route("/api/courses")
def get_courses():
    data = load_roadmap()
    if not data:
        return jsonify({"success": False, "error": "Chưa có dữ liệu"}), 404

    mon_hoc = data.get("mon_hoc", [])
    status_filter = request.args.get("status")
    if status_filter:
        mon_hoc = [c for c in mon_hoc if c.get("trang_thai") == status_filter]

    return jsonify({"success": True, "data": mon_hoc})


@app.route("/api/course/<code>")
def get_course(code):
    data = load_roadmap()
    if not data:
        return jsonify({"success": False, "error": "Chưa có dữ liệu"}), 404

    for c in data.get("mon_hoc", []):
        if c.get("ma_hp") == code:
            return jsonify({"success": True, "data": c})

    return jsonify({"success": False, "error": f"Không tìm thấy môn {code}"}), 404


@app.route("/api/groups")
def get_groups():
    """Trả trực tiếp 'khoi_summary' đã tính sẵn trong build_roadmap.py — đây
    là nguồn đúng cho tín chỉ bắt buộc/đã tích lũy theo từng khối (đã xử lý
    đúng khối tự chọn), không tính lại từ danh sách môn để tránh sai lệch.
    """
    data = load_roadmap()
    if not data:
        return jsonify({"success": False, "error": "Chưa có dữ liệu"}), 404

    khoi_summary = data.get("khoi_summary", {})
    groups = [{"ma_khoi": k, **v} for k, v in khoi_summary.items()]
    groups.sort(key=lambda x: x["ma_khoi"])

    return jsonify({"success": True, "data": groups})


@app.route("/api/advisor", methods=["POST"])
def advisor_analyze():
    """Một điểm tính duy nhất cho mục Hỗ trợ học tập.

    Body JSON:
      pace          moderate | heavy | fast
      target        kha | gioi | xuat_sac | custom
      custom_gpa    số 2.0-4.0 (khi target = custom)
      improvements  {ma_hp: điểm chữ mới}  — học cải thiện (điểm mới THAY điểm cũ)
      plan          {ma_hp: điểm chữ dự kiến} — môn mới cho kỳ tới
    Mọi thay đổi đều tính lại GPA, tín chỉ và lộ trình cùng lúc.
    """
    data = load_roadmap()
    if not data:
        return jsonify({"success": False, "error": "Chưa có dữ liệu"}), 404

    body = request.get_json(silent=True) or {}
    pace_key = body.get("pace", "moderate")
    if pace_key not in PACE_OPTIONS:
        return jsonify({"success": False, "error": "Cường độ học không hợp lệ."}), 400

    target = advisor.resolve_target(body.get("target", "gioi"), body.get("custom_gpa"))
    if target is None:
        return jsonify({
            "success": False,
            "error": f"Mục tiêu không hợp lệ. Chọn Khá/Giỏi/Xuất sắc hoặc nhập GPA từ "
                     f"{advisor.CUSTOM_MIN:.1f} đến {advisor.CUSTOM_MAX:.1f}.",
        }), 400

    def _letter_map(value):
        if not isinstance(value, dict) or len(value) > 200:
            return {}
        return {str(k): str(v) for k, v in value.items()}

    improvements = _letter_map(body.get("improvements"))
    plan = _letter_map(body.get("plan"))
    result = advisor.analyze(data, pace_key, target, improvements=improvements, plan=plan)

    # Bước 2 (phân tích hiện tại) luôn tính trên dữ liệu thật, chưa áp lựa chọn nào.
    base = result if not (improvements or plan) else advisor.analyze(data, pace_key, target)
    result["baseline"] = {
        "credits": base["credits"], "gpa": base["gpa"], "verdict": base["verdict"],
        "improvable_count": len(base["improvement"]["candidates"]),
    }
    # Gợi ý sẵn ít môn học lại nhất để đạt mục tiêu (chỉ khi cần).
    result["recommend"] = advisor.recommend_improvements(data, base)
    return jsonify({"success": True, "data": _sanitize_for_json(result)})


@app.route("/api/study-plans", methods=["GET", "POST"])
def study_plans_collection():
    """Danh sách/kho lưu kế hoạch riêng theo browser session hiện tại."""
    data = load_roadmap()
    if not data:
        return jsonify({"success": False, "error": "Cần cập nhật dữ liệu QLĐT trước khi lập kế hoạch."}), 409
    user_dir = get_user_data_dir()
    if request.method == "GET":
        return jsonify({"success": True, "data": academic_simulator.list_plans(user_dir)})
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"success": False, "error": "Dữ liệu kế hoạch không hợp lệ."}), 400
    semesters = body.get("semesters", [])
    if not isinstance(semesters, list) or len(semesters) > 18:
        return jsonify({"success": False, "error": "Kế hoạch phải có tối đa 18 học kỳ."}), 400
    saved = academic_simulator.save_plan(user_dir, body)
    validation = academic_simulator.validate_plan(data, saved)
    return jsonify({"success": True, "data": saved, "validation": validation}), 201


@app.route("/api/study-plans/simulate", methods=["POST"])
def simulate_study_plan():
    data = load_roadmap()
    if not data:
        return jsonify({"success": False, "error": "Cần cập nhật dữ liệu QLĐT trước khi mô phỏng."}), 409
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"success": False, "error": "Dữ liệu mô phỏng không hợp lệ."}), 400
    plan = {"name": str(body.get("name") or "Kế hoạch thử nghiệm")[:100], "semesters": body.get("semesters", [])}
    if not isinstance(plan["semesters"], list) or len(plan["semesters"]) > 18:
        return jsonify({"success": False, "error": "Kế hoạch phải có tối đa 18 học kỳ."}), 400
    return jsonify({"success": True, "data": academic_simulator.validate_plan(data, plan)})


@app.route("/api/study-plans/<plan_id>", methods=["PUT", "DELETE"])
def study_plan_item(plan_id):
    user_dir = get_user_data_dir()
    if request.method == "DELETE":
        if not academic_simulator.delete_plan(user_dir, plan_id):
            return jsonify({"success": False, "error": "Không tìm thấy kế hoạch."}), 404
        return jsonify({"success": True})
    data = load_roadmap()
    if not data:
        return jsonify({"success": False, "error": "Cần cập nhật dữ liệu QLĐT trước khi lưu kế hoạch."}), 409
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"success": False, "error": "Dữ liệu kế hoạch không hợp lệ."}), 400
    body["id"] = plan_id
    if not isinstance(body.get("semesters", []), list) or len(body.get("semesters", [])) > 18:
        return jsonify({"success": False, "error": "Kế hoạch phải có tối đa 18 học kỳ."}), 400
    exists = any(p.get("id") == plan_id for p in academic_simulator.list_plans(user_dir))
    if not exists:
        return jsonify({"success": False, "error": "Không tìm thấy kế hoạch."}), 404
    saved = academic_simulator.save_plan(user_dir, body)
    return jsonify({"success": True, "data": saved, "validation": academic_simulator.validate_plan(data, saved)})


@app.route("/api/graduation-check", methods=["GET", "POST"])
def graduation_check_endpoint():
    """Kiểm tra điều kiện tốt nghiệp (Điều 26 Quy chế). POST lưu phần sinh viên tự xác nhận."""
    data = load_roadmap()
    if not data:
        return jsonify({"success": False, "error": "Cần cập nhật dữ liệu QLĐT trước khi kiểm tra."}), 409
    user_dir = get_user_data_dir()
    path = user_dir / "graduation_self.json"
    allowed = set(graduation_check.SELF_DECLARED) | set(graduation_check.OVERRIDABLE)
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        raw = body.get("self_declared") if isinstance(body, dict) else None
        if not isinstance(raw, dict):
            return jsonify({"success": False, "error": "Dữ liệu tự xác nhận không hợp lệ."}), 400
        declared = {k: v for k, v in raw.items() if k in allowed and isinstance(v, bool)}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(declared, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    else:
        try:
            declared = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            declared = {}
    result = graduation_check.check_graduation(data, declared)
    return jsonify({"success": True, "data": _sanitize_for_json(result), "self_declared": declared})


@app.route("/api/refresh", methods=["POST"])
def refresh_data():
    """Chạy lại build_roadmap.py trên HTML đã crawl sẵn trong data/."""
    capture_dir = get_user_data_dir()

    try:
        html_files = [f for f in os.listdir(capture_dir) if f.endswith(".html")]
        if not html_files:
            return jsonify({"success": False, "error": "Chưa có file HTML nào. Hãy crawl trước."}), 400

        diem_file = ct_file = ketqua_file = None
        for f in html_files:
            low = f.lower()
            if "tra_c_u_i_m" in low or "diem" in low:
                diem_file = f
            elif "ch_ng_tr_nh" in low or "ctrinh" in low:
                ct_file = f
            elif "k_t_qu" in low or "ketqua" in low:
                ketqua_file = f

        script_path = os.path.join(BASE_DIR, "build_roadmap.py")
        args = [sys.executable, script_path]
        if ct_file:
            args.append(os.path.join(capture_dir, ct_file))
        if diem_file:
            args.append(os.path.join(capture_dir, diem_file))
        if ketqua_file:
            args.append(os.path.join(capture_dir, ketqua_file))

        # Chạy với cwd = thư mục riêng của user. build_roadmap.py ghi
        # roadmap.json vào cwd, nhờ vậy hai sinh viên refresh cùng lúc không
        # bao giờ tranh chấp cùng một file ở BASE_DIR.
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", timeout=120,
            cwd=str(capture_dir),
        )

        roadmap_path = capture_dir / "roadmap.json"
        if result.returncode != 0 or not roadmap_path.exists():
            # Không giữ roadmap lỗi/rỗng từ lần refresh thất bại.
            if roadmap_path.exists():
                try:
                    roadmap_path.unlink()
                except Exception:
                    pass

        ok = result.returncode == 0 and roadmap_path.exists()
        return jsonify({
            "success": ok,
            "message": "Cập nhật dữ liệu thành công" if ok
                       else "Cập nhật dữ liệu thất bại - xem chi tiết ở stderr",
            "output": result.stdout[-1000:] if result.stdout else "",
            "stderr": result.stderr[-500:] if result.stderr else "",
            "returncode": result.returncode,
        }), (200 if ok else 500)

    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "error": "Timeout - script chạy quá lâu"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============ AUTO CRAWL (profile / manual — KHÔNG có mode password) ============

def _run_crawl_job(job_id: str, mode: str, cdp_port: int = None, creds=None):
    """Chạy crawler_with_login.py, đẩy log vào _crawl_jobs[job_id]."""
    job = _crawl_jobs[job_id]
    script = os.path.join(BASE_DIR, "crawler_with_login.py")
    data_dir = Path(job["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, "-X", "utf8", script, "--mode", mode, "--outdir", str(data_dir)]

    if mode in ("profile", "cdp") and cdp_port:
        cmd += ["--cdp-port", str(cdp_port)]

    # Thông tin đăng nhập (nếu có) chỉ đi qua biến môi trường của tiến trình con,
    # không lưu vào job và không nằm trên dòng lệnh.
    child_env = None
    if creds:
        child_env = dict(job.get("crawler_env") or os.environ)
        child_env["PHENIKAA_QLDT_MSSV"] = creds[0]
        child_env.pop("PHENIKAA_QLDT_EMAIL", None)  # form ưu tiên hơn cấu hình sẵn
        child_env["PHENIKAA_QLDT_PASSWORD"] = creds[1]
    else:
        child_env = job.get("crawler_env") or None
        if child_env is None:
            child_env = dict(os.environ)
        # Không để biến cấu hình sẵn của server lọt vào phiên không nhập form.
        child_env.pop("PHENIKAA_QLDT_PASSWORD", None)

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", bufsize=1,
            env=child_env,
        )
        for line in iter(proc.stdout.readline, ""):
            line = line.rstrip()
            if not line:
                continue
            if line.startswith("LOG:"):
                try:
                    job["logs"].append(json.loads(line[4:]))
                except Exception:
                    job["logs"].append({"step": "log", "status": "info", "message": line, "progress": 0})
            else:
                job["logs"].append({"step": "log", "status": "info", "message": line, "progress": 0})

        proc.wait()
        if proc.returncode == 0:
            job["status"] = "done"
            job["logs"].append({"step": "done", "status": "ok", "message": "Hoàn tất!", "progress": 100})
        else:
            job["status"] = "error"
            job["logs"].append({"step": "done", "status": "error",
                                 "message": f"Script thoát với mã {proc.returncode}", "progress": 100})
    except Exception as e:
        job["status"] = "error"
        job["logs"].append({"step": "done", "status": "error", "message": f"Lỗi server: {e}", "progress": 100})
    finally:
        job["finished_at"] = time.time()
        if mode == "remote":
            # Trình duyệt Playwright đã đóng khi crawler kết thúc; dọn desktop
            # riêng của job để không chiếm RAM/CPU trên VPS.
            _stop_remote_desktop(job)


@app.route("/api/crawl_auto", methods=["POST"])
def crawl_auto():
    """Bắt đầu job crawl mới CHO CHÍNH user đang đăng nhập.

    Body JSON: {"mode": "profile" | "manual" | "remote" | "cdp", "cdp_port": 9222}
    ("cdp_port" chỉ dùng khi mode="cdp", mặc định 9222 nếu không truyền.)
    Tùy chọn: "mssv" + "password" để Advisor tự điền ở trang đăng nhập Microsoft
    (mssv tự thêm @st.phenikaa-uni.edu.vn). Không lưu, không log mật khẩu.
    """
    data = request.get_json(silent=True) or {}
    try:
        creds = parse_credentials(data)
    except CredentialError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    creds = creds if creds[0] else None
    mode = (data.get("mode") or "profile").lower()
    cdp_port = data.get("cdp_port")
    if mode == "cdp" and cdp_port is None:
        cdp_port = 9223

    if mode not in ALLOWED_CRAWL_MODES:
        return jsonify({
            "success": False,
            "error": f"Mode '{mode}' không được hỗ trợ qua web. Chỉ chấp nhận: {sorted(ALLOWED_CRAWL_MODES)}",
        }), 400

    if cdp_port is not None:
        try:
            cdp_port = int(cdp_port)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "cdp_port phải là số"}), 400

    _purge_old_jobs()
    client_id = get_client_id()
    data_dir = get_user_data_dir()

    # Check + create job trong cùng một lock để hai request đồng thời của cùng
    # browser không lọt qua rồi cùng ghi vào một thư mục.
    with _jobs_lock:
        for existing_id, existing in _crawl_jobs.items():
            if existing.get("client_id") == client_id and existing.get("status") == "running":
                payload = {
                    "success": False,
                    "error": "Bạn đang có một phiên crawl đang chạy.",
                    "job_id": existing_id,
                    "mode": existing.get("mode"),
                }
                if existing.get("mode") == "remote":
                    payload["remote_url"] = f"/remote_browser.html?job={existing_id}"
                return jsonify(payload), 409

        job_id = f"job_{uuid.uuid4().hex}"
        _crawl_jobs[job_id] = {
            "logs": [],
            "status": "running",
            "created_at": time.time(),
            "mode": mode,
            "client_id": client_id,
            "data_dir": str(data_dir),
        }

    job = _crawl_jobs[job_id]
    if mode == "remote":
        try:
            _start_remote_desktop(job)
        except Exception as e:
            job["status"] = "error"
            job["finished_at"] = time.time()
            _stop_remote_desktop(job)
            return jsonify({"success": False, "error": f"Không tạo được Chrome từ xa: {e}"}), 500

    t = threading.Thread(target=_run_crawl_job, args=(job_id, mode, cdp_port, creds), daemon=True)
    job["thread"] = t
    t.start()

    response = {
        "success": True,
        "job_id": job_id,
        "stream_url": f"/api/crawl_stream/{job_id}",
        "mode": mode,
    }
    if mode == "remote":
        response["remote_url"] = f"/remote_browser.html?job={job_id}"
    return jsonify(response)



if sock is not None:
    @sock.route("/ws/vnc/<job_id>")
    def vnc_websocket(ws, job_id):
        """Bridge WebSocket noVNC <-> TCP x11vnc của đúng job/session."""
        job = _get_owned_job(job_id)
        if not job or job.get("mode") != "remote" or not job.get("vnc_port"):
            try:
                ws.close(reason=1008, message="Unauthorized or missing VNC session")
            except Exception:
                pass
            return

        try:
            tcp = socket.create_connection(("127.0.0.1", int(job["vnc_port"])), timeout=5)
            tcp.settimeout(0.25)
        except OSError:
            return

        stop_event = threading.Event()

        def tcp_to_ws():
            try:
                while not stop_event.is_set():
                    try:
                        data = tcp.recv(65536)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    ws.send(data)
            except Exception:
                pass
            finally:
                stop_event.set()

        reader = threading.Thread(target=tcp_to_ws, daemon=True)
        reader.start()
        try:
            while not stop_event.is_set():
                try:
                    message = ws.receive(timeout=0.5)
                except TimeoutError:
                    continue
                except Exception:
                    break
                if message is None:
                    break
                if isinstance(message, str):
                    message = message.encode("latin1")
                tcp.sendall(message)
        except Exception:
            pass
        finally:
            stop_event.set()
            try:
                tcp.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                tcp.close()
            except Exception:
                pass


@app.route("/api/crawl_stream/<job_id>")
def crawl_stream(job_id):
    job = _get_owned_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job không tồn tại"}), 404

    def generate():
        sent_count = 0
        while True:
            logs = job["logs"]
            for log in logs[sent_count:]:
                yield f"data: {json.dumps(log, ensure_ascii=False)}\n\n"
                sent_count += 1
            if job["status"] in ("done", "error"):
                yield f"data: {json.dumps({'event': 'end', 'status': job['status']}, ensure_ascii=False)}\n\n"
                break
            time.sleep(0.3)

    return Response(generate(), mimetype="text/event-stream",
                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/crawl_status/<job_id>")
def crawl_status(job_id):
    job = _get_owned_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job không tồn tại"}), 404
    return jsonify({"success": True, "status": job["status"], "logs": job["logs"]})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 60)
    print("  PHENIKAA ADVISOR - Backend API Server (tách dữ liệu theo session)")
    print("=" * 60)
    print("  URL: http://localhost:5000")
    print(f"  Data: {DATA_ROOT} / <client_id>")
    print(f"  Remote browser: {'ON' if REMOTE_BROWSER_ENABLED else 'OFF'}")
    print()
    print("  - GET  /api/roadmap")
    print("  - GET  /api/summary")
    print("  - GET  /api/courses[?status=...]")
    print("  - GET  /api/course/<code>")
    print("  - GET  /api/groups")
    print("  - GET  /api/suggestions_next_semester?pace=moderate|heavy|fast")
    print("  - GET  /api/graduation_plan?pace=...&target=kha|gioi|xuat_sac")
    print("  - POST /api/refresh")
    print("  - POST /api/crawl_auto  {mode: 'profile'|'manual'|'remote'|'cdp', cdp_port?}")
    print("  - GET  /api/crawl_stream/<job_id>  (Server-Sent Events)")
    print("=" * 60)

    app.run(debug=False, host="0.0.0.0", port=5000)
