@echo off
chcp 65001 >nul
title 打包當鋪管理系統

echo ========================================
echo   當鋪管理系統 — 打包成可攜式程式
echo ========================================
echo.

:: 安裝 PyInstaller
echo [1/3] 安裝 PyInstaller...
pip install pyinstaller -q
if errorlevel 1 (
    echo [錯誤] 安裝 PyInstaller 失敗
    pause & exit /b 1
)

:: 清除舊的打包結果
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist "當鋪管理系統.spec" del "當鋪管理系統.spec"

:: 打包
echo [2/3] 開始打包（約需 1-3 分鐘）...
pyinstaller --onedir --console ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  --name "當鋪管理系統" ^
  --hidden-import "dateutil" ^
  --hidden-import "dateutil.relativedelta" ^
  app.py

if errorlevel 1 (
    echo.
    echo [錯誤] 打包失敗，請查看上方錯誤訊息
    pause & exit /b 1
)

:: 複製啟動說明
echo [3/3] 整理輸出資料夾...
echo 請直接執行「當鋪管理系統.exe」> "dist\當鋪管理系統\使用說明.txt"
echo 資料庫檔案「pawnshop.db」會自動建立在同一個資料夾內>> "dist\當鋪管理系統\使用說明.txt"
echo 備份方法：複製 pawnshop.db 到其他地方即可>> "dist\當鋪管理系統\使用說明.txt"

echo.
echo ========================================
echo   打包完成！
echo   輸出資料夾：dist\當鋪管理系統\
echo   將整個資料夾複製到 USB 即可
echo ========================================
echo.
explorer dist\當鋪管理系統
pause
