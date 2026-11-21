@echo off
setlocal DisableDelayedExpansion
pushd "%~dp0"
if errorlevel 1 goto folder_error

if not exist "runtime\windows-x64\python.exe" goto runtime_error
"runtime\windows-x64\python.exe" -B "start.py" --port 8770 --open-browser %*
set "FSNE_EXIT=%errorlevel%"
popd
if not "%FSNE_EXIT%"=="0" pause
endlocal & exit /b %FSNE_EXIT%

:runtime_error
echo The bundled Windows Python runtime is missing. Restore the runtime folder
echo from a complete copy of this repository, then run start-fsne.bat again.
popd
pause
endlocal & exit /b 1

:folder_error
echo Could not open the FSNE folder. Run start-fsne.bat from your repository folder.
pause
endlocal & exit /b 1
