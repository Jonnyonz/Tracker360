-- Las cantidades de los pedidos tienen que ser finitas y razonables. NUMERIC acepta 'NaN', 'Infinity' y valores
-- enormes; una sola fila asi rompia el picking de todo el deposito y el stock que leen los canales (las sumas no
-- entran en un float y la respuesta no se puede armar). La API ya las rechaza (tope MAX_CANTIDAD); esto lo asegura
-- para cualquier camino. Las comparaciones de rango tambien dejan afuera NaN e Infinity.
-- NOT VALID: no revisa las filas que ya existen (si hubiera alguna rota, se arregla a mano), pero rige
-- para todo lo que se inserte o actualice desde ahora.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'document_lines_cantidades_finitas') THEN
        ALTER TABLE document_lines ADD CONSTRAINT document_lines_cantidades_finitas
            CHECK (quantity_requested BETWEEN -1000000000000 AND 1000000000000
                   AND quantity_picked BETWEEN -1000000000000 AND 1000000000000) NOT VALID;
    END IF;
END $$;
