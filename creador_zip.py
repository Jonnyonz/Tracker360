import zipfile
import os

# Aseguramos que exista la carpeta
os.makedirs('frontend', exist_ok=True)

agente = """import time
import requests
import win32print

API_URL = "https://tracker360.mywire.org/api/admin/print-queue"
PRINTER_NAME = win32print.GetDefaultPrinter()

while True:
    try:
        res = requests.get(API_URL, timeout=5)
        if res.status_code == 200:
            for job in res.json():
                raw = job['zpl_content'].encode('utf-8')
                h = win32print.OpenPrinter(PRINTER_NAME)
                try:
                    win32print.StartDocPrinter(h, 1, ("Tracker360", None, "RAW"))
                    win32print.StartPagePrinter(h)
                    win32print.WritePrinter(h, raw)
                    win32print.EndPagePrinter(h)
                    win32print.EndDocPrinter(h)
                finally:
                    win32print.ClosePrinter(h)
                requests.put(f"{API_URL}/{job['id']}")
    except:
        pass
    time.sleep(3)
"""

instalador = """@echo off
TITLE Instalador Tracker360
color 1F
echo Instalando librerias...
pip install requests pywin32 >nul 2>&1
echo Registrando agente en Windows...
set "A=%~dp0agente_zebra.py"
schtasks /create /tn "Tracker360_Print_Agent" /tr "pythonw.exe \\"%A%\\"" /sc onlogon /rl highest /f
start pythonw.exe "%A%"
echo Instalacion completa. El agente ya esta corriendo de fondo.
pause
"""

manual = """<!DOCTYPE html>
<html lang="es">
<body style="font-family: sans-serif; padding: 20px; line-height: 1.6;">
    <h2 style="color: #1E3A8A; border-bottom: 2px solid #3B82F6;">Instalación del Agente Zebra</h2>
    <p><b>1. Instalar Python:</b> Descargue desde python.org. Es OBLIGATORIO marcar <i>"Add Python to PATH"</i>.</p>
    <p><b>2. Configurar Impresora:</b> Ponga su impresora Zebra como predeterminada en Windows.</p>
    <p><b>3. Instalar Agente:</b> Haga clic derecho en <b>instalar_agente.bat</b> y elija "Ejecutar como administrador".</p>
</body>
</html>
"""

# Comprimir todo directamente en la carpeta web
with zipfile.ZipFile('frontend/Tracker360_Print_Agent.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('agente_zebra.py', agente)
    z.writestr('instalar_agente.bat', instalador)
    z.writestr('Manual_Instalacion.html', manual)

print("✅ ¡El archivo ZIP se ha creado correctamente en la carpeta frontend!")
