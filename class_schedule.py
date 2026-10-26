"""Crawl lịch học chi tiết từng lớp học phần ở mục "Tra cứu kết quả đăng ký" (#tracuu).

Mỗi thẻ môn (``.subject-item``) có nút "Chi tiết" (``button.btnChiTietLopHocPhan``).
Bấm vào sẽ mở modal ``#myModalChiTietLich``; bảng ``#tblLichHoc`` trong modal được
nạp bằng AJAX với các cột:

    STT | Buổi học | Ngày bắt đầu | Ngày kết thúc | Thứ | Số tiết |
    Tiết bắt đầu | Tiết kết thúc | Phòng học | Giảng viên | Kiểu học

Trong đó "Ngày bắt đầu/kết thúc" là khoảng tuần, còn cột "Thứ" có dạng
``"6 - 21/08/2026"`` = thứ 6, buổi học diễn ra ngày 21/08/2026.

Cách dùng (trang #tracuu đã hiện thẻ môn):

    from class_schedule import crawl_class_schedules
    data = crawl_class_schedules(page, out_dir=Path("capture_all"))

Nút "Điểm danh" (``button.btnChiTietDiemDanh``) mở modal ``#diem_danh`` với bảng
``#tblDiemDanh`` (STT | Ngày học | Tiết bắt đầu → Tiết kết thúc | Trạng thái | Số tiết);
kết quả được gắn vào từng lớp ở khóa ``diem_danh``.

Kết quả ghi vào ``lich_hoc_phan.json``. Phần phân tích (``parse_*``) là hàm thuần,
test không cần trình duyệt.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path

OUTPUT_NAME = "lich_hoc_phan.json"

MODAL = "#myModalChiTietLich"
TABLE = "#tblLichHoc"
DETAIL_BTN = "button.btnChiTietLopHocPhan"

# Tiêu đề cột (đã bỏ dấu, chữ thường) -> khóa trong kết quả.
HEADER_KEYS = {
    "stt": "stt",
    "buoi hoc": "buoi",
    "ngay bat dau": "tuan_bat_dau",
    "ngay ket thuc": "tuan_ket_thuc",
    "thu": "thu_ngay",
    "so tiet": "so_tiet",
    "tiet bat dau": "tiet_bat_dau",
    "tiet ket thuc": "tiet_ket_thuc",
    "phong hoc": "phong",
    "giang vien": "giang_vien",
    "kieu hoc": "kieu_hoc",
}
ATT_MODAL = "#diem_danh"
ATT_TABLE = "#tblDiemDanh"
ATT_BTN = "button.btnChiTietDiemDanh"
INT_KEYS = ("stt", "so_tiet", "tiet_bat_dau", "tiet_ket_thuc")
DATE_KEYS = ("tuan_bat_dau", "tuan_ket_thuc")


# ------------------------------------------------------------------ parse

def _fold(text) -> str:
    text = unicodedata.normalize("NFD", str(text or "")).replace("đ", "d").replace("Đ", "D")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip().lower()


def _int(value):
    m = re.search(r"-?\d+", str(value or ""))
    return int(m.group()) if m else None


def parse_date(value):
    """'17/08/2026' -> '2026-08-17' (None nếu không đọc được)."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(value or ""))
    if not m:
        return None
    try:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date().isoformat()
    except ValueError:
        return None


def parse_thu_ngay(value):
    """'6 - 21/08/2026' -> (6, '2026-08-21'). Thứ theo cách gọi VN: 2=Thứ Hai ... 7=Thứ Bảy."""
    text = str(value or "")
    m = re.match(r"\s*(\d+|CN)\s*[-–]\s*(.+)$", text, flags=re.I)
    if not m:
        return None, parse_date(text)
    thu = 8 if m.group(1).upper() == "CN" else int(m.group(1))
    return thu, parse_date(m.group(2))


def parse_schedule_table(headers, rows):
    """Đổi bảng thô (danh sách tiêu đề + các dòng chuỗi) thành danh sách buổi học.

    Ánh xạ theo TÊN cột nên không hỏng nếu trường đổi thứ tự cột.
    """
    keys = [HEADER_KEYS.get(_fold(h)) for h in headers]
    sessions = []
    for cells in rows:
        if not any(str(c or "").strip() for c in cells):
            continue
        item = {}
        for key, cell in zip(keys, cells):
            if key:
                item[key] = re.sub(r"\s+", " ", str(cell or "")).strip()
        if not item.get("thu_ngay") and not item.get("tuan_bat_dau"):
            continue   # dòng "Không có dữ liệu" của DataTables
        thu, ngay = parse_thu_ngay(item.pop("thu_ngay", ""))
        out = {"thu": thu, "ngay_hoc": ngay}
        for key in INT_KEYS:
            if key in item:
                out[key] = _int(item[key])
        for key in DATE_KEYS:
            if key in item:
                out[key] = parse_date(item[key])
        for key in ("buoi", "phong", "giang_vien", "kieu_hoc"):
            if key in item:
                out[key] = item[key]
        sessions.append(out)
    return sessions


def classify_attendance(status) -> str:
    """Trạng thái điểm danh thô -> 'present' | 'absent' | 'excused' | 'other'."""
    t = _fold(status)
    if not t:
        return "other"
    if "vang" in t:
        return "excused" if "phep" in t and "khong" not in t else "absent"
    if "co mat" in t or t in ("c", "x", "present"):
        return "present"
    return "other"


def parse_attendance_table(headers, rows):
    """Bảng \"Kết quả điểm danh\" -> danh sách buổi {stt, ngay_hoc, tiet_bat_dau, tiet_ket_thuc, so_tiet, trang_thai, ket_qua}.

    Ánh xạ theo TÊN cột; cột \"Tiết bắt đầu → Tiết kết thúc\" chứa dạng ``7→9``.
    """
    folded = [_fold(h) for h in headers]

    def col(*needles):
        for i, h in enumerate(folded):
            if all(n in h for n in needles):
                return i
        return None

    i_stt, i_date = col("stt"), col("ngay")
    i_tiet, i_status, i_count = col("tiet", "bat dau"), col("trang thai"), col("so tiet")
    out = []
    for cells in rows:
        cells = [re.sub(r"\s+", " ", str(c or "")).strip() for c in cells]
        if not any(cells):
            continue

        def get(i):
            return cells[i] if i is not None and i < len(cells) else ""

        ngay = parse_date(get(i_date))
        if not ngay:
            continue   # dòng "Không có dữ liệu"
        nums = re.findall(r"\d+", get(i_tiet))
        status = get(i_status)
        out.append({
            "stt": _int(get(i_stt)),
            "ngay_hoc": ngay,
            "tiet_bat_dau": int(nums[0]) if nums else None,
            "tiet_ket_thuc": int(nums[-1]) if nums else None,
            "so_tiet": _int(get(i_count)),
            "trang_thai": status,
            "ket_qua": classify_attendance(status),
        })
    return out


def parse_card_summary(card: dict) -> dict:
    """Gộp thông tin thô của một thẻ môn (lấy bằng JS) thành bản ghi gọn."""
    details = [str(x or "").strip() for x in card.get("sum_lines", [])]
    day_lines = [str(x or "").strip() for x in card.get("day_lines", [])]

    def after(prefix):
        for line in details:
            if _fold(line).startswith(prefix):
                return _int(line.split(":", 1)[-1])
        return None

    thu_list = []
    for line in day_lines:
        if _fold(line).startswith("thu"):
            thu_list = [int(x) for x in re.findall(r"\d+", line.split(":", 1)[-1])]
    dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", " ".join(day_lines))
    subject = re.sub(r"^\s*Môn\s+", "", str(card.get("subject") or ""), flags=re.I).strip()
    return {
        "ten_hp": subject,
        "lop": str(card.get("class_name") or "").strip(),
        "ma_lop_id": str(card.get("class_id") or "").strip(),
        "loai": details[0] if details and ":" not in details[0] else None,
        "tong_so": after("tong so"),
        "da_dang_ky": after("da dang ky"),
        "tu_ngay": parse_date(dates[0]) if dates else None,
        "den_ngay": parse_date(dates[1]) if len(dates) > 1 else None,
        "thu_trong_tuan": thu_list,
        "hoc_phi": _int(str(card.get("price") or "").replace(",", "").replace(".", "")),
    }


# ------------------------------------------------------------------ browser

_CARDS_JS = """
() => Array.from(document.querySelectorAll('.subject-item')).map((el) => {
  const txt = (n) => (n ? n.textContent.trim() : '');
  const link = el.querySelector('a.btnChiTietLopHocPhan');
  const btn = el.querySelector('button.btnChiTietLopHocPhan');
  return {
    subject: txt(el.querySelector('h4')),
    class_name: link ? (link.getAttribute('title') || txt(link)) : '',
    class_id: btn ? btn.id : (link ? link.getAttribute('name') : ''),
    sum_lines: Array.from(el.querySelectorAll('.classroom-detail-sum p')).map(txt),
    day_lines: Array.from(el.querySelectorAll('.classroom-day p')).map(txt),
    price: txt(el.querySelector('.price b')),
  };
})
"""

_TABLE_JS = """
() => {
  const t = document.querySelector('#tblLichHoc');
  if (!t) return null;
  const headers = Array.from(t.querySelectorAll('thead th')).map((th) => th.textContent.trim());
  const rows = Array.from(t.querySelectorAll('tbody tr')).map(
    (tr) => Array.from(tr.querySelectorAll('td')).map((td) => td.textContent.trim()));
  const title = document.querySelector('#lblChiTietHocPhan');
  return { headers, rows, title: title ? title.textContent.trim() : '' };
}
"""


_ATT_JS = """
() => {
  const t = document.querySelector('#tblDiemDanh');
  if (!t) return null;
  const headers = Array.from(t.querySelectorAll('thead th')).map((th) => th.textContent.trim());
  const rows = Array.from(t.querySelectorAll('tbody tr')).map(
    (tr) => Array.from(tr.querySelectorAll('td')).map((td) => td.textContent.trim()));
  const title = document.querySelector('#diem_danh .zoomfileLabel');
  return { headers, rows, title: title ? title.textContent.trim() : '' };
}
"""


def _log(emit, status, message):
    if emit:
        try:
            emit("crawl", status, message)
        except Exception:
            pass


def _read_modal(page, expected_name: str, timeout_ms: int):
    """Chờ modal hiện đúng lớp `expected_name` và bảng ngừng thay đổi, rồi đọc bảng."""
    deadline = time.time() + timeout_ms / 1000
    last_count, stable = -1, 0
    empty_since = None
    snap = None
    while time.time() < deadline:
        try:
            snap = page.evaluate(_TABLE_JS)
        except Exception:
            snap = None
        if snap and (not expected_name or snap["title"] == expected_name or expected_name in snap["title"]):
            count = len(snap["rows"])
            if count == 0:
                # Tiêu đề đã đúng lớp nhưng chưa có dòng nào: sau ~3s coi là lớp chưa có lịch.
                empty_since = empty_since or time.time()
                if time.time() - empty_since > 3:
                    return snap
            else:
                empty_since = None
            if count > 0 and count == last_count:
                stable += 1
                if stable >= 2:
                    return snap
            else:
                stable = 0
            last_count = count
        page.wait_for_timeout(250)
    # Hết giờ: trả những gì đang có (có thể là lớp chưa có lịch).
    return snap if snap and (not expected_name or expected_name in snap["title"]) else None


def _read_attendance_modal(page, expected_name: str, timeout_ms: int):
    """Chờ modal điểm danh đúng lớp `expected_name` và bảng ổn định, rồi đọc bảng."""
    deadline = time.time() + timeout_ms / 1000
    last_count, stable = -1, 0
    empty_since = None
    snap = None
    while time.time() < deadline:
        try:
            snap = page.evaluate(_ATT_JS)
        except Exception:
            snap = None
        if snap and (not expected_name or expected_name in snap["title"]):
            count = len(snap["rows"])
            if count == 0:
                empty_since = empty_since or time.time()
                if time.time() - empty_since > 3:
                    return snap          # lớp chưa có dữ liệu điểm danh
            else:
                empty_since = None
            if count > 0 and count == last_count:
                stable += 1
                if stable >= 2:
                    return snap
            else:
                stable = 0
            last_count = count
        page.wait_for_timeout(250)
    return snap if snap and (not expected_name or expected_name in snap["title"]) else None


def _close_attendance_modal(page):
    try:
        page.evaluate("""() => {
            const m = document.querySelector('#diem_danh');
            const b = m && m.querySelector('.btn-close');
            if (b) b.click();
        }""")
        page.wait_for_function(
            "() => { const m = document.querySelector('#diem_danh');"
            " return !m || !m.classList.contains('show'); }", timeout=4000)
    except Exception:
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        except Exception:
            pass


def _close_modal(page):
    try:
        page.evaluate("""() => {
            const m = document.querySelector('#myModalChiTietLich');
            const b = m && m.querySelector('.btn-back, .btn-close');
            if (b) b.click();
        }""")
        page.wait_for_function(
            "() => { const m = document.querySelector('#myModalChiTietLich');"
            " return !m || !m.classList.contains('show'); }", timeout=4000)
    except Exception:
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        except Exception:
            pass


def crawl_class_schedules(page, out_dir=None, emit=None, per_class_timeout_ms: int = 10000) -> dict:
    """Bấm "Chi tiết" ở từng thẻ môn và đọc bảng lịch học trong modal.

    Trả về ``{"cap_nhat": ..., "lop": [...]}``; nếu ``out_dir`` được truyền thì ghi
    ``lich_hoc_phan.json``. Lỗi ở một lớp không làm hỏng các lớp khác.
    """
    cards = page.evaluate(_CARDS_JS) or []
    _log(emit, "info", f"Lịch học chi tiết: tìm thấy {len(cards)} lớp học phần.")
    result = []
    for idx, card in enumerate(cards):
        record = parse_card_summary(card)
        record["buoi"] = []
        record["loi"] = None
        name = record["lop"]
        try:
            # Xóa bảng cũ để không đọc nhầm lịch của lớp trước khi AJAX chưa trả về.
            page.evaluate("() => { const b = document.querySelector('#tblLichHoc tbody'); if (b) b.innerHTML = ''; }")
            page.locator(".subject-item").nth(idx).locator(DETAIL_BTN).first.click()
            snap = _read_modal(page, name, per_class_timeout_ms)
            if snap is None:
                record["loi"] = "Không đọc được bảng lịch học (hết thời gian chờ)."
            else:
                record["buoi"] = parse_schedule_table(snap["headers"], snap["rows"])
        except Exception as exc:   # một lớp lỗi thì ghi lại rồi đi tiếp
            record["loi"] = f"{type(exc).__name__}: {exc}"[:200]
        finally:
            _close_modal(page)

        # Điểm danh: lỗi ở đây không ảnh hưởng lịch học đã đọc ở trên.
        record["diem_danh"] = []
        record["diem_danh_loi"] = None
        try:
            page.evaluate("() => { const b = document.querySelector('#tblDiemDanh tbody'); if (b) b.innerHTML = ''; }")
            btn = page.locator(".subject-item").nth(idx).locator(ATT_BTN).first
            if btn.count() == 0:
                record["diem_danh_loi"] = "Không thấy nút Điểm danh của lớp này."
            else:
                btn.click()
                snap = _read_attendance_modal(page, name, per_class_timeout_ms)
                if snap is None:
                    record["diem_danh_loi"] = "Không đọc được bảng điểm danh (hết thời gian chờ)."
                else:
                    record["diem_danh"] = parse_attendance_table(snap["headers"], snap["rows"])
        except Exception as exc:
            record["diem_danh_loi"] = f"{type(exc).__name__}: {exc}"[:200]
        finally:
            _close_attendance_modal(page)

        att = record["diem_danh"]
        absent = sum(1 for a in att if a["ket_qua"] in ("absent", "excused"))
        _log(emit, "info" if not record["loi"] else "error",
             f"  {name or idx}: {len(record['buoi'])} buổi" + (f" — {record['loi']}" if record["loi"] else "")
             + f"; điểm danh {len(att)} buổi, vắng {absent}"
             + (f" — {record['diem_danh_loi']}" if record["diem_danh_loi"] else ""))
        result.append(record)

    data = {"cap_nhat": datetime.now().isoformat(timespec="seconds"), "lop": result}
    if out_dir is not None:
        path = Path(out_dir) / OUTPUT_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        _log(emit, "info", f"Đã lưu {OUTPUT_NAME}")
    return data


# ------------------------------------------------------------------ iCalendar (.ics)

# Giờ bắt đầu/kết thúc của từng tiết (theo bảng giờ của trường).
PERIOD_TIMES = {
    1: ("06:45", "07:35"), 2: ("07:40", "08:30"), 3: ("08:35", "09:25"),
    4: ("09:30", "10:20"), 5: ("10:25", "11:15"), 6: ("11:20", "12:10"),
    7: ("13:00", "13:50"), 8: ("13:55", "14:45"), 9: ("14:50", "15:40"),
    10: ("15:45", "16:35"), 11: ("16:40", "17:30"), 12: ("17:35", "18:25"),
    13: ("18:30", "19:20"), 14: ("19:20", "20:10"), 15: ("20:10", "21:00"),
}

_VTIMEZONE = (
    "BEGIN:VTIMEZONE\r\nTZID:Asia/Ho_Chi_Minh\r\nBEGIN:STANDARD\r\n"
    "DTSTART:19700101T000000\r\nTZOFFSETFROM:+0700\r\nTZOFFSETTO:+0700\r\nTZNAME:ICT\r\n"
    "END:STANDARD\r\nEND:VTIMEZONE\r\n"
)


def _ics_escape(text) -> str:
    text = str(text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return text.replace("\r\n", "\\n").replace("\n", "\\n")


def _ics_fold(line: str) -> str:
    """Gấp dòng > 75 byte theo RFC 5545 (không cắt giữa ký tự UTF-8)."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, cur, size = [], "", 0
    limit = 75
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > limit:
            parts.append(cur)
            cur, size, limit = ch, n, 74   # dòng nối bắt đầu bằng 1 dấu cách
        else:
            cur += ch
            size += n
    parts.append(cur)
    return "\r\n ".join(parts)


def build_ics(data: dict, reminder_minutes: int = 30) -> str:
    """Đổi lich_hoc_phan.json thành nội dung iCalendar (.ics).

    Mỗi buổi học = một sự kiện, UID cố định theo (lớp, ngày, tiết) nên nhập lại file
    sẽ cập nhật sự kiện cũ thay vì tạo trùng. Buổi thiếu ngày/tiết bị bỏ qua.
    """
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Phenikaa GPA Planner//Lich hoc//VI",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:Lịch học Phenikaa",
             "X-WR-TIMEZONE:Asia/Ho_Chi_Minh"]
    body = []
    for cls in data.get("lop", []):
        for b in cls.get("buoi") or []:
            date, a, z = b.get("ngay_hoc"), b.get("tiet_bat_dau"), b.get("tiet_ket_thuc")
            if not date or a not in PERIOD_TIMES or z not in PERIOD_TIMES or z < a:
                continue
            day = date.replace("-", "")
            start = f"{day}T{PERIOD_TIMES[a][0].replace(':', '')}00"
            end = f"{day}T{PERIOD_TIMES[z][1].replace(':', '')}00"
            uid_src = f"{cls.get('ma_lop_id') or cls.get('lop')}_{date}_{a}-{z}"
            uid = re.sub(r"[^A-Za-z0-9._-]", "", unicodedata.normalize("NFD", uid_src).encode("ascii", "ignore").decode()) + "@phenikaa-gpa-planner"
            desc = [f"Lớp: {cls.get('lop') or ''}", f"Tiết {a}-{z}"]
            if b.get("giang_vien"):
                desc.append(f"Giảng viên: {b['giang_vien']}")
            ev = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}",
                  f"DTSTART;TZID=Asia/Ho_Chi_Minh:{start}", f"DTEND;TZID=Asia/Ho_Chi_Minh:{end}",
                  f"SUMMARY:{_ics_escape(cls.get('ten_hp') or cls.get('lop') or 'Buổi học')}",
                  f"DESCRIPTION:{_ics_escape(chr(10).join(desc))}"]
            if b.get("phong"):
                ev.append(f"LOCATION:{_ics_escape(b['phong'])}")
            if reminder_minutes:
                ev += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Sắp đến giờ học",
                       f"TRIGGER:-PT{int(reminder_minutes)}M", "END:VALARM"]
            ev.append("END:VEVENT")
            body.append("\r\n".join(_ics_fold(x) for x in ev))
    head = "\r\n".join(_ics_fold(x) for x in lines)
    return head + "\r\n" + _VTIMEZONE + "".join(x + "\r\n" for x in body) + "END:VCALENDAR\r\n"
