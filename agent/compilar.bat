@echo off
title Compilador Tracker360
echo ===================================================
echo  Compilando Agente Tracker360...
echo ===================================================
echo.
python -m pip install -r requirements.txt pyinstaller requests pywin32 -q
python -m PyInstaller --onefile --name="Tracker360_Agente" main.py
echo.
echo ===================================================
echo COMPILACION EXITOSA. 
echo Tu ejecutable esta en la carpeta "dist".
echo ===================================================
pause