import re, subprocess, os

file_path = "frontend/admin.html"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

lines = content.splitlines()
print("=== LÍNEAS 1840 A 1865 EN admin.html (ANTES) ===")
for idx in range(max(0, 1840-1), min(len(lines), 1865)):
    print(f"{idx+1}: {lines[idx]}")

# 1. Corregir evento btnLogout
content = re.sub(
    r'(document\.getElementById\([\'"]btnLogout[\'"]\)\?\.\s*addEventListener\(\s*[\'"]click[\'"]\s*,\s*)(?:async\s*)?\(\s*\)\s*=>',
    r'\1async () =>',
    content
)

# 2. Corregir window.onload
content = re.sub(
    r'(window\.onload\s*=\s*)(?:async\s*)?(function\s*\(\)|\(\)\s*=>)',
    r'\1async () =>',
    content
)

# 3. Corregir cualquier addEventListener con arrow function o function tradicional
content = re.sub(
    r'(\.addEventListener\s*\(\s*[\'"][^\'"]+[\'"]\s*,\s*)(?:async\s*)?(\([^\)]*\)|[a-zA-Z0-9_]+)\s*=>',
    r'\1async \2 =>',
    content
)

content = re.sub(
    r'(\.addEventListener\s*\(\s*[\ me"][^\'"]+[\'"]\s*,\s*)(?:async\s*)?function\s*(\([^\)]*\))',
    r'\1async function \2',
    content
)

# Limpiar duplicaciones de async
content = content.replace("async async", "async")

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)

print("\n=== LÍNEAS 1840 A 1865 EN admin.html (DESPUÉS) ===")
lines_after = content.splitlines()
for idx in range(max(0, 1840-1), min(len(lines_after), 1865)):
    print(f"{idx+1}: {lines_after[idx]}")

# Validar la sintaxis de JavaScript con Node.js
scripts = re.findall(r'<script\b[^>]*>(.*?)</script>', content, re.DOTALL)
full_js = "\n".join(scripts)
with open("temp_check.js", "w", encoding="utf-8") as f:
    f.write(full_js)

print("\n=== VALIDACIÓN DE SINTAXIS CON NODE.JS ===")
try:
    res = subprocess.run(["node", "--check", "temp_check.js"], capture_output=True, text=True)
    if res.returncode == 0:
        print(" [OK] ¡Sintaxis de JavaScript 100% VÁLIDA sin errores!")
    else:
        print(" [!] Error detectado por Node:")
        print(res.stderr)
finally:
    if os.path.exists("temp_check.js"):
        os.remove("temp_check.js")
