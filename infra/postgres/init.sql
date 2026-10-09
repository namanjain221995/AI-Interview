-- Extensions required by the platform. Schema itself is created by Alembic
-- migrations (`alembic upgrade head`), never by application auto-creation.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
