"""Configuração lida do ambiente (.env local ou variáveis do EasyPanel)."""
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# Postgres em produção (Supabase comercial, porta 6543); sem isso, SQLite local pra desenvolver.
DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{ROOT / 'data' / 'links.db'}"
if DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL.split("://", 1)[1]

# Endereço público dos links curtos, sem barra no fim (ex.: https://go.braboconcursos.com.br)
PUBLIC_BASE = os.getenv("PUBLIC_BASE", "http://127.0.0.1:8000").rstrip("/")

# Pra onde vai quem abre um link inexistente/arquivado ou a raiz do domínio sem login
FALLBACK_URL = os.getenv("FALLBACK_URL", "https://braboconcursos.com.br")

SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_hex(32)
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))
COOKIE_SECURE = PUBLIC_BASE.startswith("https://")

# Primeiro admin: criado na subida se a tabela de usuários estiver vazia
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# Fuso usado pra agrupar cliques por dia/hora
TZ = os.getenv("TZ_NAME", "America/Sao_Paulo")

# Gravação dos cliques em lote (não bloqueia o redirecionamento)
FLUSH_SECONDS = float(os.getenv("FLUSH_SECONDS", "2"))
# Cache de redirecionamento: a cada CACHE_REFRESH_SECONDS puxa só os links alterados
# (atualizado_em); de tempo em tempo relê tudo, por garantia.
CACHE_REFRESH_SECONDS = float(os.getenv("CACHE_REFRESH_SECONDS", "10"))
CACHE_FULL_SECONDS = float(os.getenv("CACHE_FULL_SECONDS", "600"))

# Painel no Brabo Analytics (ex.: https://analytics.exemplo.com). Preenchido, o painel
# daqui (/app, /login) só redireciona pra lá e este app fica só com o redirecionamento
# dos links curtos. Vazio, o painel próprio continua funcionando.
PAINEL_URL = os.getenv("PAINEL_URL", "").strip().rstrip("/")
