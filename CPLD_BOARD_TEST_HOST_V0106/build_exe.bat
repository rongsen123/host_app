@echo off
setlocal
cd /d "%~dp0"
python -m PyInstaller --noconfirm --onefile --windowed --name SST_BoardTest_Console_v5 --distpath dist --workpath build board_test_gui.py
if errorlevel 1 exit /b 1
echo Built: %~dp0dist\SST_BoardTest_Console_v5.exe
