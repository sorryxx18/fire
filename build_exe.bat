@echo off
setlocal
cd /d "%~dp0"

echo [1/3] Installing dependencies...
py -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [2/3] Building Word template...
py template_builder.py
if errorlevel 1 goto :fail

echo [3/3] Building single Windows EXE...
py -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "消防安全表自動產製工具" ^
  --add-data "resources\template.docx;resources" ^
  --collect-all pywinauto ^
  app_ocr.py
if errorlevel 1 goto :fail

echo.
echo Done: dist\消防安全表自動產製工具.exe
pause
exit /b 0

:fail
echo.
echo Build failed. Check the error above.
pause
exit /b 1
