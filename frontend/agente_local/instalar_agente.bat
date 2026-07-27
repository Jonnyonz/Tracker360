@echo off
TITLE Instalador del Agente de Impresion - Tracker360
color 1F

echo ==========================================================
echo      INSTALADOR DEL AGENTE DE IMPRESION - TRACKER360
echo ==========================================================
echo.
echo [1/3] Verificando instalacion de Python...
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [X] ERROR CRITICO: Python no esta instalado o no fue agregado al PATH.
    echo Por favor, descargue Python desde python.org e instalelo asegurandose
    echo de marcar la casilla "Add Python to PATH" al inicio de la instalacion.
    echo.
    pause
    exit /b
)
echo [OK] Python detectado en el sistema.
echo.

echo [2/3] Instalando librerias necesarias para la comunicacion...
pip install requests pywin32
echo.

echo [3/3] Registrando la tarea automatica e invisible en Windows...
set "AGENT_PATH=%~dp0agente_zebra.py"
schtasks /create /tn "Tracker360_Print_Agent" /tr "pythonw.exe \"%AGENT_PATH%\"" /sc onlogon /rl highest /f

echo.
echo ==========================================================
echo [EXITO] La instalacion se ha completado perfectamente.
echo.
echo El agente arrancara de forma 100%% silenciosa e invisible cada
echo vez que enciendas la computadora.
echo.
echo Iniciando el agente por primera vez ahora mismo...
start pythonw.exe "%AGENT_PATH%"
echo.
pause
exit
