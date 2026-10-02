-- Publicaciones que informa cada canal de venta (por ejemplo las de Mercado Libre, que manda el middleware):
-- el canal manda la lista completa y reemplaza la anterior. Solo informativo (modulo Mercado Libre del panel).
CREATE TABLE IF NOT EXISTS channel_listings (
    sales_channel_id UUID NOT NULL REFERENCES sales_channels(id) ON DELETE CASCADE,
    listing_id VARCHAR(40) NOT NULL,
    variation_id VARCHAR(40) NOT NULL DEFAULT '',
    account VARCHAR(100),
    title TEXT,
    sku VARCHAR(100),
    status VARCHAR(40),
    quantity INT,
    problem VARCHAR(30),
    detail TEXT,
    stock_sent_at TIMESTAMPTZ,
    PRIMARY KEY (sales_channel_id, listing_id, variation_id)
);
CREATE INDEX IF NOT EXISTS channel_listings_sku_idx ON channel_listings (sales_channel_id, UPPER(sku));
ALTER TABLE sales_channels ADD COLUMN IF NOT EXISTS listings_synced_at TIMESTAMPTZ;
