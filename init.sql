-- Database initialization script for Just-EdTech
-- This script runs when the PostgreSQL container starts

-- Create the database if it doesn't exist
-- Note: This script runs in the context of the default database
-- The database is created through environment variables in docker-compose.yml

-- Create extensions that might be useful for RAG applications
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "btree_gin";

-- You can add any additional initialization here
-- For example, creating custom functions, indexes, etc.

