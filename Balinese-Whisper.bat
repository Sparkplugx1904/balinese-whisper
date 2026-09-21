@echo off
title Balinese Whisper
cd /d "%~dp0"

:: Cek ketersediaan Python
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    py --version >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        set PYTHON_CMD=py
    ) else (
        echo ========================================================
        echo  [ERROR] Python tidak ditemukan di komputer ini!
        echo ========================================================
        echo  Untuk menjalankan Balinese Whisper, pastikan Python
        echo  sudah terinstall dan opsi "Add Python to PATH" dicentang.
        echo.
        echo  Download Python di: https://www.python.org/downloads/
        echo ========================================================
        pause
        exit /b 1
    )
) else (
    set PYTHON_CMD=python
)

:: Jalankan peluncur aplikasi portable GUI
%PYTHON_CMD% app_gui.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Aplikasi berhenti dengan kode kesalahan %ERRORLEVEL%.
    pause
)
