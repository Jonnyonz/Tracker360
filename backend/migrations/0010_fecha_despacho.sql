-- Momento en que el pedido salio empacado. Lo usa el KPI "Tiempo de Ciclo Promedio" del panel (carga -> despacho).
-- Los pedidos despachados antes de esta migracion quedan en NULL y no cuentan para el promedio.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS dispatched_at TIMESTAMP WITH TIME ZONE;
