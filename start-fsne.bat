@echo off
setlocal
cd /d "%~dp0"

where uv >nul 2>&1
if errorlevel 1 (
    echo uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/
    goto :failed
)

set "PYTHON_DIR=%~dp0.fsne-python\3.11.16"
set "PYTHON_EXE=%PYTHON_DIR%\python\python.exe"
set "PYTHON_ZIP=%~dp0vendor\python-3.11.16-windows-x86_64.zip"

if not exist "%PYTHON_DIR%\.complete" (
    if not exist "%PYTHON_ZIP%" (
        echo Bundled Python archive is missing: %PYTHON_ZIP%
        goto :failed
    )
    echo Extracting bundled Python...
    powershell -NoProfile -NonInteractive -Command "Expand-Archive -LiteralPath $env:PYTHON_ZIP -DestinationPath $env:PYTHON_DIR -Force -ErrorAction Stop"
    if errorlevel 1 goto :failed
    if not exist "%PYTHON_EXE%" (
        echo The bundled Python archive did not contain python.exe.
        goto :failed
    )
    echo complete>"%PYTHON_DIR%\.complete"
)
if not exist "%PYTHON_EXE%" (
    echo The extracted Python installation is incomplete. Delete "%PYTHON_DIR%" and try again.
    goto :failed
)

echo Preparing dependencies...
call uv sync --python "%PYTHON_EXE%" --no-python-downloads
if errorlevel 1 goto :failed

echo Starting FSNE. Keep this window open while using the app.
echo Close this window to stop the server.
call uv run --no-sync --no-python-downloads python -m server --port 8770 --open-browser
if errorlevel 1 goto :failed
exit /b 0

:failed
echo.
echo FSNE could not start. Check the messages above.
pause
exit /b 1
