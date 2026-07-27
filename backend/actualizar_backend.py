import sys

with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Esquema de entrada
old_schema = 'class UserCreate(BaseModel): username: str; full_name: str; password: str; role: str = "PREPARADOR"'
new_schema = 'class UserCreate(BaseModel): username: str; full_name: str; password: str; role: str = "PREPARADOR"; email: Optional[str] = None; branch_id: Optional[str] = None; sector_id: Optional[str] = None'

# 2. Endpoint GET (Listar usuarios con JOIN)
old_get = 'rows = await conn.fetch("SELECT id, username, full_name, role, is_active FROM users ORDER BY created_at DESC")'
new_get = '''await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255); ALTER TABLE users ADD COLUMN IF NOT EXISTS branch_id VARCHAR(255); ALTER TABLE users ADD COLUMN IF NOT EXISTS sector_id VARCHAR(255);")
    rows = await conn.fetch("SELECT u.id, u.username, u.full_name, u.role, u.is_active, u.email, u.branch_id, u.sector_id, b.name as branch_name, s.name as sector_name FROM users u LEFT JOIN branches b ON u.branch_id = b.id LEFT JOIN sectors s ON u.sector_id = s.id ORDER BY u.created_at DESC")'''

# 3. Endpoint POST (Crear o Actualizar)
old_post = 'await conn.execute("INSERT INTO users (username, full_name, password_hash, role) VALUES ($1, $2, $3, $4) ON CONFLICT (username) DO UPDATE SET full_name = EXCLUDED.full_name, password_hash = EXCLUDED.password_hash, role = EXCLUDED.role, is_active = TRUE", data.username.strip().lower(), data.full_name.strip(), pwd_hash, data.role)'
new_post = 'await conn.execute("INSERT INTO users (username, full_name, password_hash, role, email, branch_id, sector_id) VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (username) DO UPDATE SET full_name = EXCLUDED.full_name, password_hash = EXCLUDED.password_hash, role = EXCLUDED.role, email = EXCLUDED.email, branch_id = EXCLUDED.branch_id, sector_id = EXCLUDED.sector_id, is_active = TRUE", data.username.strip().lower(), data.full_name.strip(), pwd_hash, data.role, data.email, data.branch_id, data.sector_id)'

cambios = 0
if old_schema in content:
    content = content.replace(old_schema, new_schema)
    cambios += 1

if old_get in content:
    content = content.replace(old_get, new_get)
    cambios += 1

if old_post in content:
    content = content.replace(old_post, new_post)
    cambios += 1

if cambios == 3:
    with open('main.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("EXITO: Se aplicaron los 3 cambios requeridos en main.py correctamente.")
else:
    print(f"ATENCION: Solo se pudieron identificar {cambios} de 3 patrones. No se modifico el archivo.")

