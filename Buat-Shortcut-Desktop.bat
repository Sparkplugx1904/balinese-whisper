@echo off
title Buat Shortcut Balinese Whisper di Desktop
cd /d "%~dp0"

echo Menyiapkan shortcut Balinese Whisper di Desktop Anda...

set TARGET=%~dp0BalineseWhisper.exe
if not exist "%TARGET%" set TARGET=%~dp0Balinese-Whisper.bat

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; " ^
  "$s = $ws.CreateShortcut([System.IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'Balinese Whisper.lnk')); " ^
  "$s.TargetPath = '%TARGET%'; " ^
  "$s.WorkingDirectory = '%~dp0'; " ^
  "$s.IconLocation = '%~dp0app.ico,0'; " ^
  "$s.Description = 'Aplikasi Balinese Whisper ASR'; " ^
  "$s.Save()"

if %ERRORLEVEL% EQU 0 (
    echo [SUKSES] Shortcut 'Balinese Whisper' berhasil dibuat di Desktop!
    echo Anda sekarang bisa membuka aplikasi langsung dari layar Desktop.
) else (
    echo [GAGAL] Gagal membuat shortcut. Silakan jalankan sebagai Administrator jika perlu.
)

ping 127.0.0.1 -n 3 >nul
