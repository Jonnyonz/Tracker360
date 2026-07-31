@echo off
title Tracker360 - Agente de Impresion Local
chcp 65001 > nul
cls

echo ===================================================
echo   Tracker360 - Agente de Impresion Local
echo ===================================================
echo.

echo [Paso 1/3] Verificando instalacion de Python...
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Python no esta instalado o no esta en el PATH.
    echo.
    pause
    exit /b 1
)

echo [Paso 2/3] Instalando librerias necesarias...
python -m pip install requests pywin32 >nul 2>&1

echo [Paso 3/3] Iniciando Agente de Impresion...
echo.
python main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR CRITICO] Ocurrio un problema en el agente.
    echo.
)

echo.
pause
