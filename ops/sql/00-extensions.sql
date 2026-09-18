-- Extensiones y schemas. Corre una sola vez, al inicializar el volumen.
-- Alembic asume que esto ya existe.

CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector: embeddings de avisos
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- búsqueda difusa de direcciones
CREATE EXTENSION IF NOT EXISTS unaccent;    -- "Córdoba" = "Cordoba"
CREATE EXTENSION IF NOT EXISTS pgcrypto;    -- cifrado de credenciales de terceros
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- Separación lógica (doc 03):
--   core   = operación (tenants, informes, propiedades a tasar)
--   corpus = avisos del mercado, compartidos entre tenants
--   eval   = backtest y métricas
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS corpus;
CREATE SCHEMA IF NOT EXISTS eval;

-- Normalización de texto, IMMUTABLE para poder usarla en columnas generadas
-- e índices. Espejo del criterio de `lead_norm_text` del panel de la inmobiliaria.
CREATE OR REPLACE FUNCTION corpus.norm_text(t text)
RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
AS $$ SELECT lower(public.unaccent('public.unaccent', t)) $$;

COMMENT ON SCHEMA core   IS 'Operación: tenants, usuarios, informes';
COMMENT ON SCHEMA corpus IS 'Avisos del mercado. org_id NULL = público/compartido';
COMMENT ON SCHEMA eval   IS 'Backtest, métricas y golden sets';
