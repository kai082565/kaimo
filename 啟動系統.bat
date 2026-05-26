@echo off
chcp 65001 >nul
title 當鋪管理系統

:: 檢查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [錯誤] 找不到 Python，請先安裝 Python 3.10 以上版本
    echo 下載網址：https://www.python.org/downloads/
    pause
    exit /b 1
)

:: 安裝套件（第一次）
echo 正在檢查相依套件...
pip install -r requirements.txt -q

:: 關掉舊的（如果有在跑）
taskkill /F /FI "WINDOWTITLE eq 當鋪管理系統" >nul 2>&1
timeout /t 1 /nobreak >nul

:: 啟動
echo 啟動當鋪管理系統...
echo 瀏覽器將自動開啟 http://127.0.0.1:5678
echo 關閉此視窗即可停止系統
echo.
python app.py

pause
