@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   JSON→MD 反向转换工具 — PyInstaller 打包
echo ============================================
echo.

REM 清理旧构建
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "*.spec" del /q "*.spec"

echo [1/3] 检查依赖...
python -c "import PyQt6; print('  PyQt6', PyQt6.QtCore.PYQT_VERSION_STR)" 2>nul || (
    echo   PyQt6 未安装，正在安装...
    pip install PyQt6 --quiet
)
python -c "import PyInstaller; print('  PyInstaller', PyInstaller.__version__)" 2>nul || (
    echo   PyInstaller 未安装，正在安装...
    pip install pyinstaller --quiet
)

echo.
echo [2/3] 打包为单文件 EXE...
python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "JSON转MD工具" ^
    --add-data "json_parser.py;." ^
    --add-data "md_writer.py;." ^
    --hidden-import PyQt6.QtCore ^
    --hidden-import PyQt6.QtGui ^
    --hidden-import PyQt6.QtWidgets ^
    --clean ^
    --noconfirm ^
    json_to_md_app.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [FAIL] 打包失败！请检查上方错误信息。
    pause
    exit /b 1
)

echo.
echo [3/3] 完成！
echo.
echo 输出文件: dist\JSON转MD工具.exe
echo.
dir "dist\JSON转MD工具.exe" 2>nul
echo.
echo 双击运行 dist\JSON转MD工具.exe 即可使用。
pause
