@echo off
title Spell Timer EXE Build

echo ============================================
echo   LoL Spell Timer - Build EXE
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python is not installed.
    echo Install from https://www.python.org/downloads/
    echo IMPORTANT: check "Add Python to PATH" during install!
    pause
    exit /b
)

echo [1/3] Installing packages...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet PyQt6 requests keyboard pyinstaller

echo [2/3] Building EXE... (takes 1-2 minutes)
python -m PyInstaller --onefile --windowed --icon "spelltimer.ico" --name "SpellTimer" lol_spell_timer.py

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Check the messages above.
    pause
    exit /b
)

echo [3/3] Cleaning up...
rmdir /s /q build >nul 2>nul
del SpellTimer.spec >nul 2>nul

echo.
echo ============================================
echo   DONE! Find SpellTimer.exe in the dist folder
echo ============================================
echo.
pause
