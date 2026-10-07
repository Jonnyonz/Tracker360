-- Indices para que el picking, el stock que leen los canales (Mercado Libre) y los reportes sigan siendo rapidos con
-- meses de pedidos y movimientos (Postgres no indexa solo las claves foraneas).
CREATE INDEX IF NOT EXISTS document_lines_documento_idx ON document_lines (document_id);
CREATE INDEX IF NOT EXISTS document_lines_sku_idx ON document_lines (UPPER(sku));
CREATE INDEX IF NOT EXISTS stock_inventory_sku_idx ON stock_inventory (UPPER(sku));
CREATE INDEX IF NOT EXISTS stock_movements_referencia_idx ON stock_movements (reference_document);
CREATE INDEX IF NOT EXISTS stock_movements_sku_fecha_idx ON stock_movements (UPPER(sku), created_at);
CREATE INDEX IF NOT EXISTS documents_estado_idx ON documents (status);
-- Pedidos abiertos: lo que miran el picking y el stock comprometido, sin importar cuanto historico haya.
CREATE INDEX IF NOT EXISTS documents_abiertos_idx ON documents (created_at) WHERE status IN ('PENDING', 'IN_PROGRESS');

-- Canales de venta: por defecto se informa el disponible MENOS lo comprometido en pedidos abiertos. Con
-- "disponible" a secas, una venta que todavia no se pickeo se vuelve a publicar y se vende dos veces. Los canales de
-- Mercado Libre existentes pasan a este modo.
ALTER TABLE sales_channels ALTER COLUMN stock_mode SET DEFAULT 'DISPONIBLE_MENOS_COMPROMETIDO';
UPDATE sales_channels SET stock_mode = 'DISPONIBLE_MENOS_COMPROMETIDO'
WHERE platform = 'MERCADOLIBRE' AND stock_mode <> 'DISPONIBLE_MENOS_COMPROMETIDO';

-- Ubicaciones: un codigo por sector. Si una base ya tiene codigos repetidos en un sector, no se crea el indice (no se
-- toca nada): la aplicacion igual rechaza los repetidos nuevos y el reporte lo muestra para corregirlo a mano.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM locations GROUP BY sector_id, UPPER(location_code) HAVING COUNT(*) > 1
    ) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS locations_sector_codigo_idx ON locations (sector_id, UPPER(location_code));
    END IF;
END $$;
