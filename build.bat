@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if exist ".desktop-build\Scripts\python.exe" (
  ".desktop-build\Scripts\python.exe" build.py %*
) else (
  py -3.10 build.py %*
)
set "BUILD_EXIT=%ERRORLEVEL%"
if not "%BUILD_EXIT%"=="0" echo Build failed. See the message and build\package-logs.
if "%~1"=="" pause
exit /b %BUILD_EXIT%
