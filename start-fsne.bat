@echo off
setlocal
cd /d "%~dp0"

where uv >nul 2>&1
if errorlevel 1 (
    echo uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/
    goto :failed
)

echo Preparing dependencies...
call uv sync
if errorlevel 1 goto :failed

if not exist "ui\index.html" (
    echo Preparing the initial dashboard. This may take a few minutes...
    call uv run python -m src movement_series --history-dir history --output output/movement_series.parquet
    if errorlevel 1 goto :failed
    call uv run python -m src movement_parents --history-dir history --output output/movement_parents.parquet
    if errorlevel 1 goto :failed
    call uv run python ui/build.py
    if errorlevel 1 goto :failed
)

echo Starting FSNE. Keep this window open while using the app.
echo Close this window to stop the server.
call uv run python -m server --port 8770 --open-browser
if errorlevel 1 goto :failed
exit /b 0

:failed
echo.
echo FSNE could not start. Check the messages above.
pause
exit /b 1
