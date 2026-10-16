-- Empaque separado del despacho (1.4.0). packed_at: cuando se empaco (se imprime la etiqueta). Con el ajuste
-- "Despacho desde Escaner Flex" el pedido queda COMPLETED + packed_at ("esperando carga") hasta que el Escaner
-- informa la carga en una ola; sin el ajuste, empacar sigue despachando en el mismo paso.
-- dispatch_ref: de donde salio (ola, patente y quien cargo, segun lo informa el Escaner).
ALTER TABLE documents ADD COLUMN IF NOT EXISTS packed_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS dispatch_ref TEXT;
CREATE INDEX IF NOT EXISTS documents_shipment_ref_idx ON documents (shipment_ref) WHERE shipment_ref IS NOT NULL;
