from main import engine
from sqlalchemy import text

with engine.begin() as conn:
    columnas_items = ["weight", "length", "width", "height", "volume"]
    for col in columnas_items:
        try:
            conn.execute(text(f"ALTER TABLE items ADD COLUMN {col} FLOAT;"))
            print(f"Columna '{col}' agregada a 'items' con éxito.")
        except Exception as e:
            print(f"Nota: La columna '{col}' posiblemente ya existe.")

    try:
        conn.execute(text("ALTER TABLE system_settings ADD COLUMN enable_item_dimensions BOOLEAN DEFAULT FALSE;"))
        print("Columna 'enable_item_dimensions' agregada a 'system_settings' con éxito.")
    except Exception as e:
        print("Nota: La columna 'enable_item_dimensions' ya existe.")
