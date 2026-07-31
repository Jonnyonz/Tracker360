@echo off
title Tracker360 - Crear EXE e Inicio Automatico
echo ===================================================
echo  Creando Ejecutable (.EXE) e Inicio Automatico
echo ===================================================
echo.
echo [1/3] Instalando librerias y PyInstaller...
python -m pip install -r requirements.txt pyinstaller -q
echo.
echo [2/3] Compilando main.py a Tracker360_Agente.exe...
python -m PyInstaller --onefile --noconsole --name="Tracker360_Agente" main.py
echo.
echo [3/3] Configurando inicio automatico al encender la PC...
if exist "dist\Tracker360_Agente.exe" (
    copy /y "dist\Tracker360_Agente.exe" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Tracker360_Agente.exe"
    echo.
    echo ===================================================
    echo ¡PROCESO COMPLETADO EXITOSAMENTE!
    echo.
    echo 1. Se creo el ejecutable independiente en:
    echo    dist\Tracker360_Agente.exe
    echo.
    echo 2. Se instalo en el Inicio de Windows.
    echo    El agente arrancara en segundo plano automaticamente
    echo    cada vez que encienda la computadora.
    echo ===================================================
) else (
    echo.
    echo ===================================================
    echo [ERROR CRITICO] No se pudo compilar el archivo EXE.
    echo El proceso fue abortado para proteger el sistema.
    echo ===================================================
)
echo.
pause
