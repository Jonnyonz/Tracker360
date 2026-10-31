-- Ajustes de stock (1.8.0): un documento con numero correlativo (AJ-000001), sucursal, sector, concepto y lineas
-- con la cantidad firmada (+ suma, - resta). Cada linea genera un movimiento 'AJUSTE' con el numero como referencia.
CREATE TABLE IF NOT EXISTS stock_adjustments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    adjustment_number VARCHAR(50) UNIQUE NOT NULL,
    branch_id UUID NOT NULL REFERENCES branches(id),
    sector_id UUID NOT NULL REFERENCES sectors(id),
    reason VARCHAR(100) NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_by VARCHAR(50) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS stock_adjustment_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    adjustment_id UUID NOT NULL REFERENCES stock_adjustments(id) ON DELETE CASCADE,
    sku VARCHAR(100) NOT NULL,
    quantity NUMERIC NOT NULL,
    location_id UUID REFERENCES locations(id),
    lot_number VARCHAR(100) NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_stock_adjustments_created ON stock_adjustments (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_stock_adjustment_lines_adj ON stock_adjustment_lines (adjustment_id);
CREATE INDEX IF NOT EXISTS idx_stock_adjustment_lines_sku ON stock_adjustment_lines (UPPER(sku));
-- Conceptos de ajuste que ofrece la pantalla (uno por linea); se editan en Configuracion.
INSERT INTO system_settings (key, value) VALUES ('stock_adjustment_reasons', E'Rotura\nVencimiento\nFaltante\nSobrante\nError de carga\nConsumo interno\nOtro') ON CONFLICT (key) DO NOTHING;
