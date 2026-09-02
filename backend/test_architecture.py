from fastapi.testclient import TestClient

try:
    from backend.main import app
except ImportError:
    from main import app

client = TestClient(app)

def test_modular_architecture_compiles():
    # 1. Al solicitar el esquema OpenAPI, FastAPI compila internamente todos los routers.
    # Si hay un error de importación en inbound.py o outbound.py, esto lanzará una excepción y el test fallará.
    response = client.get("/openapi.json")
    assert response.status_code == 200, "El servidor no pudo compilar el árbol de rutas."
    
    schema = response.json()
    schema_str = str(schema)
    
    # 2. Verificamos que los 5 nuevos dominios logísticos estén inyectados en el núcleo
    dominios_esperados = [
        "Inbound & Receptions", 
        "Outbound & Dispatch", 
        "Internal Movements", 
        "Inventory Control", 
        "Dashboard & Logs"
    ]
    
    for dominio in dominios_esperados:
        assert dominio in schema_str, f"Fallo arquitectónico: El módulo '{dominio}' no se registró en main.py"

def test_frontend_statics_mounted():
    # Verificamos que el frontend siga respondiendo sin chocar con la API
    response = client.get("/index.html")
    assert response.status_code in [200, 303], "Los archivos estáticos del frontend no están montados correctamente."
