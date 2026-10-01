-- Outbox de webhooks: el evento (stock.updated, despacho) se guarda en la MISMA transaccion que el
-- cambio que lo origina y un proceso en segundo plano lo envia despues del commit, con reintentos.
-- Antes se enviaba en el momento, dentro de la transaccion: el destino podia recibirlo antes de que
-- el cambio estuviera confirmado, o recibir un cambio que despues se deshacia (S8).
CREATE TABLE IF NOT EXISTS webhook_outbox (
    id BIGSERIAL PRIMARY KEY,
    channel_id UUID NOT NULL REFERENCES integration_channels(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempts INT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS webhook_outbox_pendientes_idx ON webhook_outbox (next_attempt_at)
    WHERE delivered_at IS NULL AND failed_at IS NULL;
