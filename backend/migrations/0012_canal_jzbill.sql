-- Canal de venta de tipo JZBILL (1.5.1): el facturador JZBill se conecta con una clave de canal para importar el
-- catalogo (GET /api/v1/channel/items). No es una tienda online: no aparece en Configuracion, Tiendas online, ni
-- tiene modulo de publicaciones.
ALTER TABLE sales_channels DROP CONSTRAINT IF EXISTS sales_channels_platform_check;
ALTER TABLE sales_channels ADD CONSTRAINT sales_channels_platform_check
    CHECK (platform IN ('MERCADOLIBRE', 'TIENDANUBE', 'WOOCOMMERCE', 'SHOPIFY', 'PRESTASHOP', 'EMPRETIENDA', 'JZBILL'));
