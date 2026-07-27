import zipfile, os
from fpdf import FPDF

# Asegurar que existe la carpeta frontend
os.makedirs('frontend', exist_ok=True)

# 1. Crear el PDF con las instrucciones (Adaptado a fpdf2 moderno)
class PDF(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 14)
        self.set_text_color(30, 58, 138)
        self.cell(0, 10, 'GUIA DE DESPLIEGUE - AGENTE ZEBRA TRACKER360', 0, align='C')
        self.ln(10)
        self.line(10, 20, 200, 20)
        self.ln(5)

pdf = PDF()
pdf.add_page()
pdf.set_font('Helvetica', '', 10)

texto = """1. REQUISITOS PREVIOS (OBLIGATORIOS)
------------------------------------------------------------------------------------------------------
A) Instalar Python: Descargue la ultima version desde python.org. Durante la instalacion, 
   es estrictamente OBLIGATORIO marcar la casilla inferior que dice "Add Python to PATH".
B) Configurar Impresora: Ingrese a la configuracion de Windows, desmarque la opcion "Dejar que
   Windows administre mi impresora predeterminada" y establezca su Zebra como predeterminada.

2. INSTALACION AUTOMATICA (Metodo Recomendado)
------------------------------------------------------------------------------------------------------
Para un despliegue rapido, utilice el script desatendido incluido en este paquete:
1. Extraiga TODOS los archivos de este ZIP en una carpeta fija (Ejemplo: C:\\Tracker360).
2. Haga clic derecho sobre 'instalar_agente.bat' y seleccione 'Ejecutar como administrador'.
3. La consola instalara las dependencias y dejara el agente corriendo de forma invisible.

3. INSTALACION MANUAL (Crear la tarea en Windows)
------------------------------------------------------------------------------------------------------
Si el instalador automatico es bloqueado por politicas de seguridad, configure el agente 
manualmente siguiendo estos pasos exactos:

Paso 1: Extraiga este ZIP en una carpeta permanente (Ejemplo: C:\\Tracker360).
Paso 2: Presione la tecla Windows, escriba 'cmd', abra la consola y ejecute el comando
        para instalar las librerias: pip install requests pywin32
Paso 3: Presione la tecla Windows y abra la herramienta "Programador de tareas".
Paso 4: En el panel derecho, haga clic en "Crear tarea..." (No utilice "Crear tarea basica").
Paso 5: En la pestana [General]:
        - Nombre: Agente de Impresion Tracker360
        - Marque la casilla: "Ejecutar con los privilegios mas altos".
Paso 6: En la pestana [Desencadenadores]:
        - Haga clic en "Nuevo...".
        - En Iniciar la tarea, seleccione: "Al iniciar la sesion". Acepte.
Paso 7: En la pestana [Acciones]:
        - Haga clic en "Nuevo...". Accion: "Iniciar un programa".
        - En Programa o script escriba: pythonw.exe
        - En Agregar argumentos escriba la ruta del script entre comillas: "C:\\Tracker360\\agente_zebra.py"
        - En Iniciar en (opcional) ponga la carpeta raiz: C:\\Tracker360\\
Paso 8: Guarde la tarea. 

A partir de ahora, el agente arrancara silenciosamente cada vez que se encienda la PC. 
Para iniciarlo en este momento sin tener que reiniciar, haga clic derecho sobre su nueva 
tarea en la lista y seleccione "Ejecutar".
"""

# Pasamos el texto completo para que fpdf2 lo gestione
pdf.multi_cell(0, 6, texto)
pdf.output('Manual_Instalacion_Zebra.pdf')

# 2. Recrear el codigo del agente
agente = "import time\nimport requests\nimport win32print\n\nAPI_URL = \"https://tracker360.mywire.org/api/admin/print-queue\"\nPRINTER_NAME = win32print.GetDefaultPrinter()\n\nwhile True:\n    try:\n        res = requests.get(API_URL, timeout=5)\n        if res.status_code == 200:\n            for job in res.json():\n                raw = job['zpl_content'].encode('utf-8')\n                h = win32print.OpenPrinter(PRINTER_NAME)\n                try:\n                    win32print.StartDocPrinter(h, 1, (\"Tracker360\", None, \"RAW\"))\n                    win32print.StartPagePrinter(h)\n                    win32print.WritePrinter(h, raw)\n                    win32print.EndPagePrinter(h)\n                    win32print.EndDocPrinter(h)\n                finally:\n                    win32print.ClosePrinter(h)\n                requests.put(f\"{API_URL}/{job['id']}\")\n    except:\n        pass\n    time.sleep(3)"

# 3. Recrear el instalador BAT
instalador = "@echo off\nTITLE Instalador Tracker360\ncolor 1F\necho [1/3] Verificando instalacion de Python...\npython --version >nul 2>&1\nIF %ERRORLEVEL% NEQ 0 ( echo ERROR: Python no esta instalado o falta en el PATH. & pause & exit /b )\necho [2/3] Instalando librerias...\npip install requests pywin32 >nul 2>&1\necho [3/3] Registrando tarea invisible en Windows...\nset \"A=%~dp0agente_zebra.py\"\nschtasks /create /tn \"Tracker360_Print_Agent\" /tr \"pythonw.exe \\\"%A%\\\"\" /sc onlogon /rl highest /f\nstart pythonw.exe \"%A%\"\necho.\necho [EXITO] Instalacion completa. El agente ya esta operando de fondo.\npause"

# 4. Comprimir los 3 archivos
with zipfile.ZipFile('frontend/Tracker360_Print_Agent.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('agente_zebra.py', agente)
    z.writestr('instalar_agente.bat', instalador)
    z.write('Manual_Instalacion_Zebra.pdf')

# Limpiar PDF temporal
os.remove('Manual_Instalacion_Zebra.pdf')
print("✅ ¡Archivo ZIP con PDF creado con éxito!")
