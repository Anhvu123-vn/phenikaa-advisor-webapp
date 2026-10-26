"""Kiểm tra mã sinh viên + mật khẩu do người dùng nhập ở form crawl.

Chỉ validate, KHÔNG lưu và KHÔNG ghi log. Mật khẩu chỉ đi từ request tới tiến
trình crawler (qua biến môi trường của tiến trình con) rồi bị crawler xóa khỏi
environ ngay khi khởi động.
"""
import re

EMAIL_DOMAIN = "st.phenikaa-uni.edu.vn"
_MSSV_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
MAX_PASSWORD_LEN = 256


class CredentialError(ValueError):
    """Lỗi dữ liệu nhập (thông báo an toàn để trả cho người dùng)."""


def build_email(mssv: str) -> str:
    return f"{mssv}@{EMAIL_DOMAIN}"


def parse_credentials(data: dict):
    """Trả (mssv, password) hoặc (None, None) nếu người dùng không nhập gì.

    - Người dùng chỉ cần nhập mã sinh viên; nếu lỡ nhập cả
      ``@st.phenikaa-uni.edu.vn`` thì vẫn chấp nhận.
    - Nhập thiếu một trong hai -> CredentialError.
    """
    mssv = str(data.get("mssv") or "").strip()
    password = data.get("password")
    password = "" if password is None else str(password)

    if not mssv and not password:
        return None, None
    if not mssv or not password:
        raise CredentialError("Hãy nhập đủ cả mã sinh viên và mật khẩu.")

    if "@" in mssv:
        local, _, domain = mssv.partition("@")
        if domain.lower() != EMAIL_DOMAIN:
            raise CredentialError(
                f"Chỉ cần nhập mã sinh viên (hệ thống tự thêm @{EMAIL_DOMAIN})."
            )
        mssv = local

    if not _MSSV_RE.match(mssv):
        raise CredentialError("Mã sinh viên không hợp lệ.")
    if len(password) > MAX_PASSWORD_LEN or "\n" in password or "\r" in password:
        raise CredentialError("Mật khẩu không hợp lệ.")
    return mssv, password
