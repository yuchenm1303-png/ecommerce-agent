@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo Ecommerce-Agent Test Lab - Offline Full
echo ========================================
echo.

python tools\test_lab.py --all --json-report logs\test-lab\latest.json
set EXIT_CODE=%ERRORLEVEL%

echo.
if %EXIT_CODE%==0 (
    echo TEST LAB PASSED
) else (
    echo TEST LAB FAILED - review the failing pytest output above.
)
echo Report: logs\test-lab\latest.json
echo.
pause
exit /b %EXIT_CODE%
