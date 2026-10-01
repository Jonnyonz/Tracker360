-- Canales de venta (marketplaces, tiendas): un sistema externo (por ejemplo el middleware de Mercado
-- Libre) que carga pedidos en Tracker y recibe el stock. Cada canal tiene su propia clave de API (solo
-- se guarda su hash) y su configuracion de stock.
CREATE TABLE IF NOT EXISTS sales_channels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(30) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    api_key_hash TEXT UNIQUE NOT NULL,
    -- DISPONIBLE: stock operativo. DISPONIBLE_MENOS_COMPROMETIDO: ademas descuenta lo pedido que todavia
    -- no se pickeo en pedidos abiertos.
    stock_mode VARCHAR(30) NOT NULL DEFAULT 'DISPONIBLE'
        CHECK (stock_mode IN ('DISPONIBLE', 'DISPONIBLE_MENOS_COMPROMETIDO')),
    -- Sucursales cuyo stock se informa al canal; NULL = todas.
    stock_branch_ids UUID[],
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ
);

-- Pedido que vino de un canal: su referencia en el canal (numero de venta), la cuenta del canal y el
-- envio. El numero de Tracker (document_number) sigue siendo el correlativo.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS sales_channel_id UUID REFERENCES sales_channels(id);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS external_ref VARCHAR(100);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS external_account VARCHAR(100);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS shipping_type VARCHAR(30);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS shipment_ref VARCHAR(100);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS channel_label_zpl TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS documents_canal_ref_idx ON documents (sales_channel_id, external_ref)
    WHERE sales_channel_id IS NOT NULL;

-- Eventos para cada canal (cambios de stock, pedidos empacados o despachados). El canal los lee con un
-- cursor (el id): no hace falta que Tracker le haga webhooks a la red interna.
CREATE TABLE IF NOT EXISTS channel_events (
    id BIGSERIAL PRIMARY KEY,
    sales_channel_id UUID NOT NULL REFERENCES sales_channels(id) ON DELETE CASCADE,
    event_type VARCHAR(40) NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS channel_events_canal_idx ON channel_events (sales_channel_id, id);
