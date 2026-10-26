"""Chạy thử toàn bộ luồng đăng nhập tự động trên trang GIẢ LẬP (QLĐT -> Microsoft -> ADFS)
bằng Chromium thật. Không cần tài khoản thật.
  python e2e_mock_login.py            # biến thể bình thường
  VARIANT=clear python e2e_mock_login.py    # trang xóa giá trị nếu không có phím thật
  VARIANT=overlay python e2e_mock_login.py  # nút Sign in bị che
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PHENIKAA_QLDT_MSSV"] = "23010334"
os.environ["PHENIKAA_QLDT_PASSWORD"] = "Correct#1" if os.environ.get("VARIANT")!="wrong" else "Wrong#1"
import crawler_with_login as c
from playwright.sync_api import sync_playwright

LOGIN = "https://qldtbeta.phenikaa-uni.edu.vn/congsinhvien/login.aspx"
MS = "https://login.microsoftonline.com/0eff/oauth2/v2.0/authorize?client_id=53"
ADFS = "https://sso.phenikaa-uni.edu.vn/adfs/ls/?client-request-id=abc&wa=wsignin1.0"
OK = "https://qldtbeta.phenikaa-uni.edu.vn/congsinhvien/index.aspx"

VARIANT = os.environ.get("VARIANT","plain")
VH = {"overlay": '<div style="position:fixed;inset:0;z-index:9"></div>'}.get(VARIANT,"")
VJ = {"clear": """var lastKey=0; document.addEventListener('keydown',()=>lastKey=Date.now());
   document.getElementById('passwordInput').addEventListener('input',e=>{ if(Date.now()-lastKey>200) e.target.value=''; });"""}.get(VARIANT,"")
PAGES = {
 "login": f"""<html><body><h1>ĐĂNG NHẬP</h1>
   <button type="button" onclick="location='{MS}'"><img src=x width=1 height=1> Sign in using Microsoft</button></body></html>""",
 "ms": f"""<html><title>Sign in to your account</title><body>
   <input type="email" name="loginfmt" id="i0116" placeholder="account@phenikaa-uni.edu.vn">
   <input type="password" name="passwd" id="i0118" style="display:none">
   <input type="submit" id="idSIButton9" value="Next"
     onclick="if(document.getElementById('i0116').value.endsWith('@st.phenikaa-uni.edu.vn')) location='{ADFS}'; return false;"></body></html>""",
 "adfs": f"""<html><title>Sign In</title><body>
   <form id="loginForm" onsubmit="return false">
   <input id="userNameInput" name="UserName" type="email" value="23010334@st.phenikaa-uni.edu.vn">
   <input id="passwordInput" name="Password" type="password" tabindex="2" class="text fullWidth" placeholder="Mot de passe" autocomplete="off">
   <span id="submitButton" class="submit" role="button" tabindex="4" onclick="return go();">Sign in</span>
   <span id="errorText" class="error"></span></form>
   VARIANT_HTML
   <script>
   VARIANT_JS
   document.getElementById('passwordInput').addEventListener('keypress', e => {{ if (e.keyCode==13) go(); }});
   function go() {{ var p=document.getElementById('passwordInput').value;
      if (p==='Correct#1') location='{OK}'; else document.getElementById('errorText').innerText='Incorrect user ID or password.'; return false; }}
   </script></body></html>""",
 "ok": """<html><body><div id="sidebar-menu"><div class="sidebar-menu-item">Điểm</div></div></body></html>""",
}
VARIANT = os.environ.get("VARIANT","normal")
if VARIANT == "keys_only":   # JS xóa ô mật khẩu nếu giá trị không đến từ phím thật
    PAGES["adfs"] = PAGES["adfs"].replace("<script>", """<script>
   (function(){ var el=document.getElementById('passwordInput'), real=false;
     el.addEventListener('keydown',function(){real=true;});
     el.addEventListener('input',function(){ if(!real) el.value=''; });
   })();""",1)
if VARIANT == "overlay":     # nút Sign in bị lớp phủ che -> click chuột thường bị chặn
    PAGES["adfs"] = PAGES["adfs"].replace("</body>", '<div style="position:fixed;inset:0;background:rgba(0,0,0,0)"></div></body>')
if VARIANT == "wrong":
    os.environ["PHENIKAA_QLDT_PASSWORD"] = "x"
def handler(route):
    u = route.request.url
    key = ("login" if "login.aspx" in u else "ms" if "microsoftonline" in u
           else "adfs" if "sso.phenikaa" in u else "ok")
    body = PAGES[key].replace("VARIANT_HTML", VH).replace("VARIANT_JS", VJ)
    route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

logs = []
c.emit = lambda step, status, msg, prog=0: (logs.append(f"[{status}] {msg}"), print(f"[{status}] {msg}"))

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(); ctx.route("**/*", handler)
    page = ctx.new_page(); page.goto(LOGIN)
    c.reset_microsoft_signin_state(); c.reset_microsoft_username_state(); c.reset_microsoft_password_state()
    c.emit_autofill_status()
    t0 = time.time(); result = None
    while time.time() - t0 < 25:
        if c.has_menu(page): result = "MENU OK"; break
        acted = False
        for cand in c._all_live_pages(ctx, page):
            if c.click_microsoft_signin(cand) or c.fill_microsoft_username(cand) or c.fill_microsoft_password(cand):
                page = cand; acted = True; break
        if not acted:
            for cand in c._all_live_pages(ctx, page): c._maybe_diagnose(cand)
        time.sleep(0.4)
    print("RESULT:", result or ("TIMEOUT at " + page.url))
    b.close()
