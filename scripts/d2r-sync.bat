@echo off
setlocal
cd /d "%~dp0.."

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY set "PY=python"

if "%~1"=="" (
    %PY% -m d2rsync sync
) else (
    %PY% -m d2rsync %*
)
set "RC=%ERRORLEVEL%"

if not "%D2R_SYNC_NO_PAUSE%"=="1" (
    echo.
    pause
)
exit /b %RC%
