@echo off
title NSE Scanner Hub
cd /d "%~dp0"
echo.
echo   NSE SCANNER HUB  -  Patterns / Supply ^& Demand / Volume
echo   Starting local server at http://127.0.0.1:8080
echo   (Keep this window open. Close it to stop the server.)
echo.
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8080"
where python >nul 2>nul
if %errorlevel%==0 (
  python app.py
) else (
  py -3 app.py
)
echo.
echo   Server stopped.
pause