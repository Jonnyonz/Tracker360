-- Tiendas online: cada canal de venta es de una tienda (Mercado Libre, Tiendanube, WooCommerce, Shopify,
-- PrestaShop, Empretienda). Los canales que ya existian son del middleware de Mercado Libre.
ALTER TABLE sales_channels ADD COLUMN IF NOT EXISTS platform VARCHAR(20) NOT NULL DEFAULT 'MERCADOLIBRE';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sales_channels_platform_check') THEN
        ALTER TABLE sales_channels ADD CONSTRAINT sales_channels_platform_check
            CHECK (platform IN ('MERCADOLIBRE', 'TIENDANUBE', 'WOOCOMMERCE', 'SHOPIFY', 'PRESTASHOP', 'EMPRETIENDA'));
    END IF;
END $$;

-- Cada tienda se activa en Configuracion (store_<tienda>_enabled) y recien ahi aparece su modulo en el
-- menu. Vienen desactivadas, salvo Mercado Libre en una instalacion que ya tiene canales: su modulo se
-- veia siempre y no tiene que desaparecer al actualizar.
INSERT INTO system_settings (key, value)
SELECT 'store_mercadolibre_enabled', 'true' WHERE EXISTS (SELECT 1 FROM sales_channels)
ON CONFLICT (key) DO NOTHING;
