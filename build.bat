@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo === 清理上次打包残留 ===
if exist dist                    rmdir /s /q dist
if exist build                   rmdir /s /q build
if exist "绵阳城市学院校园网登录.spec"  del /q "绵阳城市学院校园网登录.spec"

echo === 正在打包 campus_login.py ===
pyinstaller --noconfirm --onefile --windowed ^
    --name "绵阳城市学院校园网登录" ^
    --hidden-import winotify ^
    --hidden-import PyQt5.sip ^
    campus_login.py

echo.
echo === 打包完成，输出在 dist\ 目录 ===
pause
