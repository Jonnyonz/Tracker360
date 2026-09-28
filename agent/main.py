import sys, os, time, json, traceback, logging, subprocess
import secrets, hashlib, base64, socket, webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode, quote
import requests

# 1. RUTAS SEGURAS (Blindaje contra permisos de Windows)
appdata_path = os.getenv('APPDATA')
app_folder = os.path.join(appdata_path, 'Tracker360Agent')

if not os.path.exists(app_folder):
    os.makedirs(app_folder)

CONFIG_FILE = os.path.join(app_folder, "config.json")
LOG_FILE = os.path.join(app_folder, "agente_error.log")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.ERROR,
    format='%(asctime)s %(levelname)s: %(message)s'
)

def get_windows_printers():
    try:
        cmd = ["powershell", "-Command", "Get-Printer | Select-Object -ExpandProperty Name"]
        result = subprocess.run(cmd, capture_output=True, text=True)
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
            print("   [!] Error: El contenido ZPL recibido para la impresora esta vacio.")
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

# === LOGIN POR NAVEGADOR ===
LOGIN_TIMEOUT_SECONDS = 300

CALLBACK_PAGE = """<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><title>Tracker360 Agente</title>
<style>body{{background:#f4f6f9;color:#212529;font-family:system-ui,-apple-system,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
.card{{background:#fff;padding:2.5rem;border-radius:12px;box-shadow:0 4px 12px rgba(0,0,0,.05);border:1px solid #e0e0e0;max-width:420px;text-align:center}}
h2{{font-size:1.3rem;margin:0 0 .6rem}}p{{color:#666;font-size:.9rem;line-height:1.5;margin:0}}</style></head>
<body><div class="card"><h2>{title}</h2><p>{message}</p></div></body></html>"""

def _pkce_pair():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge

def browser_login(server_url, agent_name):
    """Abre el navegador para iniciar sesion en Tracker360 y devuelve el token del agente."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    result = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            qs = parse_qs(parsed.query)
            if qs.get("state", [""])[0] != state:
                title, message = "Solicitud invalida", "La respuesta no corresponde a este agente. Vuelva a intentarlo desde la aplicacion."
            elif "code" in qs:
                result["code"] = qs["code"][0]
                title, message = "Listo", "El agente de impresion fue autorizado. Ya puede cerrar esta ventana y volver a la aplicacion."
            else:
                result["error"] = qs.get("error", ["access_denied"])[0]
                title, message = "Autorizacion cancelada", "No se autorizo el agente. Puede cerrar esta ventana."
            body = CALLBACK_PAGE.format(title=title, message=message).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
    server.timeout = 1
    port = server.server_address[1]
    query = urlencode({"port": port, "state": state, "challenge": challenge, "name": agent_name}, quote_via=quote)
    auth_url = f"{server_url}/agent-auth.html?{query}"

    print("\n--> Abriendo el navegador para iniciar sesion en Tracker360...")
    print("    Si no se abre solo, copie esta direccion en su navegador:")
    print(f"    {auth_url}\n")
    webbrowser.open(auth_url)

    deadline = time.time() + LOGIN_TIMEOUT_SECONDS
    try:
        while "code" not in result and "error" not in result and time.time() < deadline:
            server.handle_request()
    finally:
        server.server_close()

    if "code" not in result:
        if "error" in result:
            raise RuntimeError("La autorizacion fue cancelada en el navegador.")
        raise RuntimeError("Se agoto el tiempo de espera para iniciar sesion.")

    res = requests.post(f"{server_url}/api/print-agent/token",
                        json={"code": result["code"], "code_verifier": verifier}, timeout=10)
    if res.status_code != 200:
        raise RuntimeError("El servidor rechazo la autorizacion del agente. Vuelva a intentarlo.")
    data = res.json()
    print(f"--> Agente autorizado por '{data.get('authorized_by')}'.\n")
    return data["token"]

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)

def default_agent_name(queue_code):
    return f"{socket.gethostname()} - {queue_code or 'RECEPCION'}"

def load_or_create_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                print("--> Configuracion guardada detectada:")
                print(f"    * Servidor: {cfg.get('server_url')}")
                print(f"    * Sector / Cola: {cfg.get('queue_code')}")
                print(f"    * Impresora: {cfg.get('printer_name')}")
                print("")
                ans = input("¿Desea usar esta configuracion? (S/n): ").strip().lower()
                if ans != 'n':
                    return cfg
        except Exception as e:
            logging.error(f"No se pudo leer la configuracion guardada: {e!r}")
            print("--> La configuracion guardada no se pudo leer. Se creara una nueva.")

    print("\n===================================================")
    print("   ASISTENTE DE CONFIGURACION DE IMPRESION")
    print("===================================================")

    # URL sugerida por cliente: variable de entorno TRACKER360_SERVER_URL (opcional).
    default_url = os.getenv("TRACKER360_SERVER_URL", "").strip()
    prompt = f"1. URL del Servidor [{default_url}]: " if default_url else "1. URL del Servidor (Ej: https://wms.suempresa.com): "
    server_url = ""
    while not server_url:
        server_url = input(prompt).strip() or default_url
        if server_url and not server_url.startswith(("https://", "http://")):
            print("   La URL debe empezar con https://")
            server_url = ""

    queue_code = input("2. Codigo de Sector / Cola (Ej: RECEPCION): ").strip().upper()

    printers = get_windows_printers()
    printer_name = ""
    if printers:
        print("\nImpresoras detectadas en Windows:")
        for idx, p in enumerate(printers, 1):
            print(f"   [{idx}] {p}")
        
        choice = input(f"Seleccione numero (1-{len(printers)}) o nombre exacto: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(printers):
            printer_name = printers[int(choice) - 1]
        else:
            printer_name = choice
    
    if not printer_name:
        printer_name = input("3. Nombre exacto de la impresora Zebra/Windows: ").strip()

    server_url = server_url.rstrip("/")
    cfg = {
        "server_url": server_url,
        "queue_code": queue_code,
        "printer_name": printer_name
    }

    print("\n4. Acceso al servidor")
    api_key = input("   Presione Enter para iniciar sesion en el navegador (o escriba una Clave API de Sistema): ").strip()
    if api_key:
        cfg["api_key"] = api_key
    else:
        cfg["agent_token"] = browser_login(server_url, default_agent_name(queue_code))

    save_config(cfg)

    print("\nConfiguracion guardada exitosamente\n")
    return cfg

def auth_headers(cfg):
    if cfg.get("agent_token"):
        return {"Authorization": f"Bearer {cfg['agent_token']}"}
    return {"X-API-Key": cfg.get("api_key", "")}

def run_agent():
    cfg = load_or_create_config()
    server_url = cfg["server_url"]
    queue_code = cfg["queue_code"]
    printer_name = cfg["printer_name"]

    if not cfg.get("agent_token") and not cfg.get("api_key"):
        cfg["agent_token"] = browser_login(server_url, default_agent_name(queue_code))
        save_config(cfg)

    headers = auth_headers(cfg)
    endpoint_jobs = f"{server_url}/api/print-agent/jobs?queue_code={queue_code}"

    print(f"--> Conectado a la cola '{queue_code}' con impresora '{printer_name}'")
    print("--> Escuchando trabajos de impresion... (Puede minimizar esta ventana)")
    print("---------------------------------------------------\n")

    while True:
        try:
            res = requests.get(endpoint_jobs, headers=headers, timeout=5)
            if res.status_code in (401, 403):
                # Acceso revocado o vencido: volver a iniciar sesion en el navegador.
                print("\n[!] El servidor rechazo el acceso del agente. Es necesario volver a iniciar sesion.")
                try:
                    token = browser_login(server_url, default_agent_name(queue_code))
                except RuntimeError as e:
                    print(f"[!] {e} Reinicie el agente para volver a intentarlo.")
                    break
                cfg.pop("api_key", None)
                cfg["agent_token"] = token
                save_config(cfg)
                headers = auth_headers(cfg)
                continue
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
                            print(f"    -> Trabajo {job_id} impreso con exito.")
            
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