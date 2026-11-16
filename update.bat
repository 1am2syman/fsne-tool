@echo off
setlocal
cd /d "%~dp0"

where git >nul 2>&1
if errorlevel 1 (
    echo Git is required to update FSNE.
    goto :failed
)

echo Updating FSNE from the configured Git upstream...
git pull --ff-only
if errorlevel 1 goto :failed

echo.
echo Update complete. Run start-fsne.bat to start FSNE.
pause
exit /b 0

:failed
echo.
echo Update failed. Check the messages above; your local files have not been reset.
pause
exit /b 1
