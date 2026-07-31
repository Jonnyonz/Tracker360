# Tracker360 - Agente de Impresión Local
import sys, os, time, json, traceback, logging, subprocess
import requests

CONFIG_FILE = "config.json"

logging.basicConfig(
    filename='agente_error.log',
    level=logging.ERROR,
    format='%(asctime)s %(levelname)s: %(message)s'
)

def get_windows_printers():
    try:
        # Utilizamos PowerShell nativo para asegurar la lectura de drivers Zebra/Brother
        cmd = 'powershell -Command "Get-Printer | Select-Object -ExpandProperty Name"'
        result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        if result.returncode == 0:
            printers = [line.strip() for line in result.stdout.split('\n') if line.strip()]
            return printers
        return []
    except Exception:
        return []

def send_zpl_to_printer(printer_name, zpl_content):
    try:
        import win32print

        zpl_str = str(zpl_content) if zpl_content else ""
        
        if "\n" in zpl_str:
            zpl_str = zpl_str.replace("\n", "")

        if not zpl_str.strip():
            print("   [!] Error: El contenido ZPL recibido para la impresora está vacío.")
            return False

        hPrinter = win32print.OpenPrinter(printer_name)
        try:
            hJob = win32print.StartDocPrinter(hPrinter, 1, ("Etiqueta Tracker360", None, "RAW"))
            win32print.StartPagePrinter(hPrinter)
            win32print.WritePrinter(hPrinter, zpl_str.encode('utf-8'))
            win32print.EndPagePrinter(hPrinter)
            win32print.EndDocPrinter(hPrinter)
            return True
        finally:
            win32print.ClosePrinter(hPrinter)
    except Exception as e:
        print(f"   [!] Error al enviar datos a la impresora '{printer_name}': {e}")
        return False

def load_or_create_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                print("--> Configuración guardada detectada:")
                print(f"    * Servidor: {cfg.get('server_url')}")
                print(f"    * Sector / Cola: {cfg.get('queue_code')}")
                print(f"    * Impresora: {cfg.get('printer_name')}")
                print("")
                ans = input("¿Desea usar esta configuración? (S/n): ").strip().lower()
                if ans != 'n':
                    return cfg
        except Exception:
            pass

    print("\n===================================================")
    print("    ASISTENTE DE CONFIGURACION DE IMPRESION")
    print("===================================================")

    server_url = input("1. URL del Servidor [https://tracker360.mywire.org]: ").strip()
    if not server_url:
        server_url = "https://tracker360.mywire.org"

    api_key = input("2. Clave API de Sistema (Header X-API-Key): ").strip()
    queue_code = input("3. Codigo de Sector / Cola (Ej: RECEPCION): ").strip().upper()

    printers = get_windows_printers()
    printer_name = ""
    if printers:
        print("\nImpresoras detectadas en Windows:")
        for idx, p in enumerate(printers, 1):
            print(f"   [{idx}] {p}")
        
        # Corrección: El input ahora está fuera del bucle for
        choice = input(f"Seleccione número (1-{len(printers)}) o nombre exacto: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(printers):
            printer_name = printers[int(choice) - 1]
        else:
            printer_name = choice
    
    if not printer_name:
        printer_name = input("4. Nombre exacto de la impresora Zebra/Windows: ").strip()

    cfg = {
        "server_url": server_url.rstrip("/"),
        "api_key": api_key,
        "queue_code": queue_code,
        "printer_name": printer_name
    }

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)

    print("\nConfiguración guardada en 'config.json'\n")
    return cfg

def run_agent():
    cfg = load_or_create_config()
    server_url = cfg["server_url"]
    api_key = cfg["api_key"]
    queue_code = cfg["queue_code"]
    printer_name = cfg["printer_name"]

    headers = {"X-API-Key": api_key}
    endpoint_jobs = f"{server_url}/api/print-agent/jobs?queue_code={queue_code}"

    print(f"--> Conectado a la cola '{queue_code}' con impresora '{printer_name}'")
    print("--> Escuchando trabajos de impresion... (Presione Ctrl + C para salir)")
    print("---------------------------------------------------\n")

    while True:
        try:
            res = requests.get(endpoint_jobs, headers=headers, timeout=5)
            if res.status_code == 200:
                jobs = res.json()
                if jobs:
                    for job in jobs:
                        job_id = job.get("id")
                        zpl = job.get("zpl") or job.get("zpl_content") or ""
                        print(f"[+] Procesando trabajo {job_id}...")
                        
                        if send_zpl_to_printer(printer_name, zpl):
                            ack_url = f"{server_url}/api/print-agent/jobs/{job_id}/ack"
                            requests.post(ack_url, headers=headers, timeout=5)
                            print(f"    -> Trabajo {job_id} impreso con éxito.")
            
            time.sleep(3)
        except KeyboardInterrupt:
            print("\nServicio detenido por el usuario.")
            break
        except Exception as e:
            logging.error(f"Error en consulta: {e}")
            time.sleep(5)

if __name__ == "__main__":
    try:
        run_agent()
    except Exception as e:
        error_msg = traceback.format_exc()
        logging.error(error_msg)
        print("\n---------------------------------------------------")
        print("ERROR AL EJECUTAR EL AGENTE:")
        print(e)
        print("---------------------------------------------------")
        sys.exit(1)
