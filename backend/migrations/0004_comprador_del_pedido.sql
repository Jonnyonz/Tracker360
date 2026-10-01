-- Comprador de un pedido que viene de un canal de venta (por ejemplo Mercado Libre): no es una entidad
-- dada de alta en Tracker, asi que su nombre y direccion quedan en el propio pedido. Si el pedido tiene
-- cliente (customer_id), se sigue mostrando el cliente.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS buyer_name VARCHAR(200);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS buyer_address TEXT;
