import os
import pytest

import crawler_with_login as c
from qldt_credentials import CredentialError, parse_credentials


# ---------- parse_credentials ----------
def test_empty_means_no_credentials():
    assert parse_credentials({}) == (None, None)
    assert parse_credentials({"mssv": "  ", "password": ""}) == (None, None)


def test_plain_mssv_ok():
    assert parse_credentials({"mssv": " 21010001 ", "password": "x y"}) == ("21010001", "x y")


def test_full_student_email_is_stripped():
    assert parse_credentials({"mssv": "21010001@ST.phenikaa-uni.edu.vn", "password": "p"})[0] == "21010001"


@pytest.mark.parametrize("data", [
    {"mssv": "21010001", "password": ""},
    {"mssv": "", "password": "p"},
    {"mssv": "a@gmail.com", "password": "p"},
    {"mssv": "bad id!", "password": "p"},
    {"mssv": "ab", "password": "p"},
    {"mssv": "21010001", "password": "a\nb"},
    {"mssv": "21010001", "password": "x" * 300},
])
def test_invalid_inputs(data):
    with pytest.raises(CredentialError):
        parse_credentials(data)


# ---------- fake page ----------
class Box:
    def __init__(self, v=""):
        self.v = v
        self.pressed = False

    def is_visible(self): return True
    def input_value(self): return self.v
    def fill(self, x): self.v = x
    def press(self, k): self.pressed = True
    def click(self, **kw): pass
    def focus(self): pass


class Btn:
    def __init__(self): self.clicks = 0
    def is_visible(self): return True
    def click(self, **kw): self.clicks += 1


class MsPage:
    def __init__(self, kind, url="https://login.microsoftonline.com/x", error=False):
        self.url, self.kind, self.error = url, kind, error
        self.user, self.pw, self.btn = Box(), Box(), Btn()

    def query_selector(self, sel):
        if "passwordError" in sel or "usernameError" in sel:
            return Btn() if self.error else None
        if "errorText" in sel:
            return None
        if sel == "#userNameInput":
            return self.user if self.kind == "adfs" else None
        if "loginfmt" in sel:
            return self.user if self.kind == "user" else None
        if "passwd" in sel or "passwordInput" in sel:
            return self.pw if self.kind in ("pass", "adfs") else None
        return self.btn

    def wait_for_timeout(self, ms): pass


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(c, "emit", lambda *a, **k: None)
    monkeypatch.delenv("PHENIKAA_QLDT_EMAIL", raising=False)
    monkeypatch.setenv("PHENIKAA_QLDT_MSSV", "21010001")
    c._CRED["password"] = "s3cret"
    c.reset_microsoft_username_state()
    c.reset_microsoft_password_state()
    yield
    c._CRED["password"] = ""


def test_username_gets_student_domain():
    p = MsPage("user")
    assert c.fill_microsoft_username(p)
    assert p.user.v == "21010001@st.phenikaa-uni.edu.vn"
    assert p.btn.clicks == 1


def test_password_filled_once_only():
    p = MsPage("pass")
    assert c.fill_microsoft_password(p)
    assert p.pw.v == "s3cret"
    p.pw.v = ""
    assert not c.fill_microsoft_password(p)  # không thử lần 2


def test_wrong_password_stops_retrying():
    p = MsPage("pass", error=True)
    assert c.fill_microsoft_password(p)
    assert c._ms_pass_state["failed"] is True
    c._ms_pass_state["count"] = 0
    p.pw.v = ""
    assert not c.fill_microsoft_password(p)


def test_no_password_configured_does_nothing():
    c._CRED["password"] = ""
    assert not c.fill_microsoft_password(MsPage("pass"))


def test_only_on_microsoft_domain():
    assert not c.fill_microsoft_password(MsPage("pass", url="https://evil.example.com/"))
    assert not c.fill_microsoft_username(MsPage("user", url="https://evil.example.com/"))


def test_password_never_emitted(monkeypatch):
    logs = []
    monkeypatch.setattr(c, "emit", lambda *a, **k: logs.append(" ".join(map(str, a))))
    c.fill_microsoft_password(MsPage("pass", error=True))
    assert logs and all("s3cret" not in line for line in logs)


def test_password_removed_from_environ_on_import():
    import importlib
    os.environ["PHENIKAA_QLDT_PASSWORD"] = "zzz"
    importlib.reload(c)
    assert "PHENIKAA_QLDT_PASSWORD" not in os.environ
    assert c._CRED["password"] == "zzz"


ADFS_URL = "https://sso.phenikaa-uni.edu.vn/adfs/ls/?client-request-id=abc"


def test_adfs_password_filled_and_submitted():
    p = MsPage("adfs", url=ADFS_URL)
    p.user.v = "21010001@st.phenikaa-uni.edu.vn"   # Microsoft đã điền sẵn
    assert c.fill_microsoft_password(p)
    assert p.pw.v == "s3cret"
    assert p.btn.clicks == 1
    assert p.user.v == "21010001@st.phenikaa-uni.edu.vn"  # không bị ghi đè


def test_adfs_empty_username_gets_filled():
    p = MsPage("adfs", url=ADFS_URL)
    assert c.fill_microsoft_password(p)
    assert p.user.v == "21010001@st.phenikaa-uni.edu.vn"


def test_adfs_error_text_stops_retry():
    class ErrPage(MsPage):
        def query_selector(self, sel):
            if "errorText" in sel:
                class E(Btn):
                    def inner_text(s): return "Incorrect user ID or password."
                return E()
            return super().query_selector(sel)
    p = ErrPage("adfs", url=ADFS_URL)
    assert c.fill_microsoft_password(p)
    assert c._ms_pass_state["failed"] is True


def test_adfs_url_is_not_kicked_back_to_qldt():
    assert c._is_auth_url(ADFS_URL)
    assert c._is_auth_url("https://login.microsoftonline.com/x")
    assert not c._is_auth_url("https://qldtbeta.phenikaa-uni.edu.vn/congsinhvien/login.aspx")
    assert not c._is_auth_url("https://phenikaa-uni.edu.vn/")


def test_status_message_never_contains_password(monkeypatch):
    logs = []
    monkeypatch.setattr(c, "emit", lambda *a, **k: logs.append(a[2]))
    c.emit_autofill_status()
    assert logs and "BẬT" in logs[0] and "s3cret" not in logs[0]
    c._CRED["password"] = ""
    logs.clear()
    c.emit_autofill_status()
    assert "chưa có mật khẩu" in logs[0]


def test_diagnose_lists_inputs_once(monkeypatch):
    logs = []
    monkeypatch.setattr(c, "emit", lambda *a, **k: logs.append(a[2]))

    class P(MsPage):
        def evaluate(self, js): return "input#passwordInput[name=Password,type=password,hiện]"

    p = P("adfs", url=ADFS_URL)
    c._auth_seen["sso.phenikaa-uni.edu.vn"] = 0  # đã thấy từ lâu
    c._maybe_diagnose(p)
    c._maybe_diagnose(p)
    assert len(logs) == 1 and "passwordInput" in logs[0] and "s3cret" not in logs[0]
