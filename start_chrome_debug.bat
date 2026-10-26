@echo off
REM ============================================================
REM start_chrome_debug.bat
REM Bat co --remote-debugging-port de crawler co the gan (CDP) vao
REM dung Chrome nay va mo tab moi, thay vi mo mot cua so Chrome
REM tach biet.
REM
REM CANH BAO: Chrome KHONG THE bat co nay khi dang chay - phai dong
REM HET cua so Chrome hien tai roi Windows moi cho phep mo lai kem
REM co. Script nay se tu dong dong Chrome (--restore-last-session se
REM co gang mo lai cac tab dang mo, nhung khong dam bao 100%%).
REM Hay luu cong viec dang do truoc khi chay script nay.
REM ============================================================

set PORT=9222

echo Dang dong Chrome hien tai...
taskkill /F /IM chrome.exe /T >nul 2>&1
timeout /t 2 /nobreak >nul

set CHROME_PATH="C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist %CHROME_PATH% set CHROME_PATH="C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

echo Dang mo lai Chrome voi cong debug %PORT%...
start "" %CHROME_PATH% --remote-debugging-port=%PORT% --restore-last-session

echo Xong. Chrome da mo lai voi CDP bat tren port %PORT%.
echo Gio ban co the dang nhap QLDT (neu chua) va bam "Crawl" (che do CDP) tren web app.
pause
