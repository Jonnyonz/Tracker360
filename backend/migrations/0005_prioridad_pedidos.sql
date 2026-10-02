-- Prioridad de preparacion de los pedidos: 1 = urgente (por ejemplo una venta Flex de Mercado Libre, que
-- se entrega en el dia). Los urgentes salen primero en la lista de picking y en las olas.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS priority SMALLINT NOT NULL DEFAULT 0;
