@echo off
setlocal

set "APP_ROOT=%~dp0"
set "CONFIG_DIR=%APPDATA%\TokenScope"

if not exist "%CONFIG_DIR%" mkdir "%CONFIG_DIR%"
if not exist "%CONFIG_DIR%\config.ini" copy "%APP_ROOT%config.example.ini" "%CONFIG_DIR%\config.ini" >nul

"%APP_ROOT%TokenScopeServer\TokenScopeServer.exe" --host 0.0.0.0 --port 8765 --interval 300 --config "%CONFIG_DIR%\config.ini" --open-browser
exit /b %ERRORLEVEL%
