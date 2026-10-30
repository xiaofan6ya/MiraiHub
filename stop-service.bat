@echo off
title workbuddy2api - Stop
cd /d "%~dp0"
setlocal

echo ========================================
echo   workbuddy2api Stop Service
echo ========================================
echo.

REM 同时停掉守护进程与服务（含残留 main.py）
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\daemon.ps1" -Stop

netstat -ano | findstr ":8790" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo Failed to stop! Trying taskkill fallback...
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8790" ^| findstr "LISTENING"') do (
        taskkill /PID %%a /F >nul 2>&1
        echo Killed PID=%%a
    )
) else (
    echo Service stopped.
)

echo.
pause
