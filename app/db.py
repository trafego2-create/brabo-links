"""Tabelas e conexão. Mesmo código roda em Postgres (produção) e SQLite (local/testes).

Em Postgres as tabelas ficam no schema `encurtador`; o SQLite não tem schema, então o
nome é removido via schema_translate_map.
"""
from sqlalchemy import (
    BigInteger, Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, MetaData,
    SmallInteger, String, Table, Text, create_engine,
)

from . import config

SCHEMA = "encurtador"
metadata = MetaData(schema=SCHEMA)

# BigInteger não vira autoincremento no SQLite; a variante resolve isso
BigId = BigInteger().with_variant(Integer(), "sqlite")

users = Table(
    "usuarios", metadata,
    Column("id", Integer, primary_key=True),
    Column("email", String(200), nullable=False, unique=True),
    Column("nome", String(120), nullable=False, default=""),
    Column("senha_hash", String(200), nullable=False),
    Column("papel", String(20), nullable=False, default="editor"),  # admin | editor | leitura
    Column("ativo", Boolean, nullable=False, default=True),
    Column("criado_em", DateTime(timezone=True), nullable=False),
)

links = Table(
    "links", metadata,
    Column("id", Integer, primary_key=True),
    Column("slug", String(80), nullable=False, unique=True),
    Column("destino", Text, nullable=False),
    Column("titulo", String(200), nullable=False, default=""),
    Column("categoria", String(30), nullable=False, default="pagina"),
    Column("campanha", String(60), nullable=False, default=""),
    Column("ativo", Boolean, nullable=False, default=True),
    Column("criado_por", String(200), nullable=False, default=""),
    Column("criado_em", DateTime(timezone=True), nullable=False),
    Column("atualizado_em", DateTime(timezone=True), nullable=False),
)

clicks = Table(
    "cliques", metadata,
    Column("id", BigId, primary_key=True),
    Column("link_id", Integer, ForeignKey(f"{SCHEMA}.links.id", ondelete="CASCADE"), nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("dia", Date, nullable=False),          # dia no fuso de São Paulo
    Column("hora", SmallInteger, nullable=False),  # 0-23 no fuso de São Paulo
    Column("visitante", String(16), nullable=False),  # hash de IP+navegador (não guarda o IP)
    Column("bot", Boolean, nullable=False, default=False),
    Column("origem", String(30), nullable=False, default="Direto"),
    Column("referer_host", String(120), nullable=False, default=""),
    Column("dispositivo", String(20), nullable=False, default=""),
    Column("sistema", String(30), nullable=False, default=""),
    Column("navegador", String(30), nullable=False, default=""),
    Column("pais", String(2), nullable=False, default=""),
    Column("utm_source", String(100), nullable=False, default=""),
    Column("utm_medium", String(100), nullable=False, default=""),
    Column("utm_campaign", String(150), nullable=False, default=""),
    Column("utm_content", String(150), nullable=False, default=""),
    Column("ref", String(120), nullable=False, default=""),  # ?ref= para rastrear por pessoa
    Index("cliques_link_dia_idx", "link_id", "dia"),
    Index("cliques_dia_idx", "dia"),
)


def make_engine(url: str | None = None):
    url = url or config.DATABASE_URL
    if url.startswith("sqlite"):
        from pathlib import Path
        path = url.split("///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        eng = create_engine(url, connect_args={"check_same_thread": False})
        return eng.execution_options(schema_translate_map={SCHEMA: None})
    # Pooler do Supabase em modo transação (6543): sem prepared statements, pool pequeno —
    # o banco comercial tem limite baixo de clientes somados entre todos os apps.
    return create_engine(
        url,
        pool_size=2, max_overflow=2, pool_pre_ping=True, pool_recycle=300,
        connect_args={"prepare_threshold": None, "connect_timeout": 10},
    )
