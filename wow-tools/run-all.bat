@echo off
echo.
echo ============================================================
echo   WoW Addon Release -- Full Workflow
echo   Step 1: Update TOC files and commit
echo   Step 2: Create and push release tags
echo ============================================================
echo.

python "%~dp0update-toc.py"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: update-toc.py exited with an error.
    echo Check the output above before proceeding.
    echo push-release.py will NOT run.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   TOC update complete. Starting release tagging...
echo ============================================================
echo.

python "%~dp0push-release.py"

echo.
echo ============================================================
echo   Done.
echo ============================================================
pause
