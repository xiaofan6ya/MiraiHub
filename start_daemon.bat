@echo off
REM ===========================================================================
REM workbuddy2api 守护启动脚本
REM ---------------------------------------------------------------------------
REM 为什么需要这个：
REM   直接把 python main.py 挂在某个终端/工具的后台会话里跑，会话一结束
REM   进程就被连带回收（表现为日志最后一行还是 200 OK，但进程没了）。
REM   本脚本用 PowerShell 的 Start-Process 把服务脱离当前会话启动，
REM   并带一个守护循环：进程掉了自动拉起。
REM
REM 用法：
REM   start_daemon.bat          启动（后台，关掉本窗口也会继续跑）
REM   start_daemon.bat stop     停止服务
REM ===========================================================================
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\daemon.ps1" %*
endlocal
