"""Acesso ao banco: links, usuários, cliques (em lote) e estatísticas."""
import logging
import re
import threading
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, insert, inspect, select, text, update

from . import config, security
from .db import SCHEMA, clicks, links, metadata, users

log = logging.getLogger("links")
TZ = ZoneInfo(config.TZ)

CATEGORIAS = {
    "youtube": ("YouTube", "ti-brand-youtube"),
    "vendas": ("Página de vendas", "ti-shopping-cart"),
    "checkout": ("Checkout", "ti-credit-card"),
    "ebook": ("E-book / Material", "ti-book"),
    "whatsapp": ("WhatsApp", "ti-brand-whatsapp"),
    "pagina": ("Outro", "ti-link"),
}
PAPEIS = {"admin": "Admin", "editor": "Editor", "leitura": "Só leitura"}
UTMS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
_CACHE_COLS = ("id", "slug", "destino", "ativo", *UTMS)

# Caminhos do próprio painel — não podem virar link curto
RESERVADOS = {
    "app", "api", "login", "logout", "static", "healthz", "admin", "favicon.ico",
    "robots.txt", "img", "qr", "links", "usuarios",
}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


def agora() -> datetime:
    return datetime.now(timezone.utc)


def hoje() -> date:
    return datetime.now(TZ).date()


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60].strip("-")


def normaliza_slug(s: str) -> str:
    return (s or "").strip().strip("/").lower()


def valida_slug(slug: str) -> str | None:
    if not slug:
        return "Escolha um nome para o link."
    if slug in RESERVADOS:
        return f"“{slug}” é reservado pelo sistema. Escolha outro nome."
    if not SLUG_RE.match(slug):
        return "Use só letras minúsculas, números, - e _ (sem espaço nem acento)."
    return None


def normaliza_destino(url: str) -> str:
    url = (url or "").strip()
    if url and not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    return url


def valida_destino(url: str) -> str | None:
    if not url:
        return "Cole o link de destino."
    if not re.match(r"^https?://[^\s/$.?#][^\s/?#]*\.[a-z]{2,}(?::\d+)?([/?#]\S*)?$", url, re.I):
        return "Esse não parece um link válido. Confira se copiou o endereço inteiro (ex.: https://site.com/pagina)."
    if url.split("://", 1)[1].lower().startswith(config.PUBLIC_BASE.split("://", 1)[-1].lower() + "/"):
        return "O destino não pode ser outro link curto daqui (daria voltas infinitas)."
    return None


def detecta_categoria(url: str) -> str:
    u = (url or "").lower()
    if re.search(r"(youtube\.com|youtu\.be)", u):
        return "youtube"
    if re.search(r"(wa\.me|whatsapp\.com)", u):
        return "whatsapp"
    if re.search(r"(pay\.hotmart|checkout|/pay/|eduzz|kiwify\.com\.br/|tmb\.|pagamento)", u):
        return "checkout"
    if re.search(r"(\.pdf($|\?)|drive\.google|docs\.google|ebook|e-book|material|apostila)", u):
        return "ebook"
    if re.search(r"(braboconcursos|aprovasim|hotmart\.com|/vendas|/oferta|/inscricao|lp\.)", u):
        return "vendas"
    return "pagina"


# ── Banco ─────────────────────────────────────────────────────────────────────

class Store:
    def __init__(self, engine):
        self.engine = engine
        self.pg = engine.dialect.name == "postgresql"
        self._cache: dict[str, dict] = {}
        self._cache_lock = threading.Lock()
        self._buffer: list[dict] = []
        self._buf_lock = threading.Lock()

    # Estrutura
    def init(self) -> None:
        with self.engine.begin() as c:
            if self.pg:
                c.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        metadata.create_all(self.engine)
        self._colunas_novas()
        if self.pg:
            # RLS ligado sem policy = chave anon/authenticated do Supabase não lê nada.
            # O app conecta como dono das tabelas, que ignora RLS.
            with self.engine.begin() as c:
                for t in ("usuarios", "links", "cliques"):
                    c.execute(text(f"ALTER TABLE {SCHEMA}.{t} ENABLE ROW LEVEL SECURITY"))
            try:
                with self.engine.begin() as c:
                    c.execute(text(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM anon, authenticated"))
            except Exception:
                log.info("papéis anon/authenticated não existem (não é Supabase); seguindo")
        if config.ADMIN_EMAIL and config.ADMIN_PASSWORD:
            with self.engine.begin() as c:
                vazio = c.execute(select(func.count()).select_from(users)).scalar() == 0
            if vazio:
                self.criar_usuario(config.ADMIN_EMAIL, "Admin", config.ADMIN_PASSWORD, "admin")
                log.info("admin inicial criado: %s", config.ADMIN_EMAIL)
        self.recarregar_cache()

    def _colunas_novas(self) -> None:
        """create_all não altera tabela existente: acrescenta as colunas que faltarem."""
        insp = inspect(self.engine)
        esquema = SCHEMA if self.pg else None
        for tabela in (links, clicks):
            existentes = {c["name"] for c in insp.get_columns(tabela.name, schema=esquema)}
            for col in tabela.columns:
                if col.name in existentes:
                    continue
                nome = f"{SCHEMA}.{tabela.name}" if self.pg else tabela.name
                tipo = col.type.compile(self.engine.dialect)
                with self.engine.begin() as c:
                    c.execute(text(f"ALTER TABLE {nome} ADD COLUMN {col.name} {tipo} NOT NULL DEFAULT ''"))
                log.info("coluna criada: %s.%s", tabela.name, col.name)

    # ── Cache de redirecionamento ──
    def recarregar_cache(self) -> None:
        with self.engine.connect() as c:
            rows = c.execute(select(*[links.c[k] for k in _CACHE_COLS])).mappings().all()
        novo = {r["slug"]: dict(r) for r in rows}
        with self._cache_lock:
            self._cache = novo

    def resolver(self, slug: str) -> dict | None:
        with self._cache_lock:
            hit = self._cache.get(slug)
        if hit is not None:
            return hit
        # criado por outra instância desde o último refresh
        with self.engine.connect() as c:
            r = c.execute(select(*[links.c[k] for k in _CACHE_COLS])
                          .where(links.c.slug == slug)).mappings().first()
        if r:
            with self._cache_lock:
                self._cache[slug] = dict(r)
            return dict(r)
        return None

    def _cache_put(self, row: dict) -> None:
        with self._cache_lock:
            for k in [k for k, v in self._cache.items() if v["id"] == row["id"]]:
                del self._cache[k]
            self._cache[row["slug"]] = {k: row[k] for k in _CACHE_COLS}

    # ── Cliques ──
    def registrar(self, clique: dict) -> None:
        with self._buf_lock:
            self._buffer.append(clique)

    def gravar_cliques(self) -> int:
        with self._buf_lock:
            lote, self._buffer = self._buffer, []
        if not lote:
            return 0
        try:
            with self.engine.begin() as c:
                c.execute(insert(clicks), lote)
            return len(lote)
        except Exception:
            log.exception("falha gravando %d cliques; voltam pra fila", len(lote))
            with self._buf_lock:
                # mantém no máximo 50 mil pendentes se o banco ficar fora muito tempo
                self._buffer = (lote + self._buffer)[-50_000:]
            return 0

    # ── Usuários ──
    def criar_usuario(self, email: str, nome: str, senha: str, papel: str) -> int:
        with self.engine.begin() as c:
            return c.execute(insert(users).values(
                email=email.strip().lower(), nome=nome.strip(), senha_hash=security.hash_password(senha),
                papel=papel, ativo=True, criado_em=agora(),
            ).returning(users.c.id)).scalar_one()

    def usuario(self, uid: int) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(users).where(users.c.id == uid)).mappings().first()
        return dict(r) if r else None

    def usuario_por_email(self, email: str) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(users).where(users.c.email == email.strip().lower())).mappings().first()
        return dict(r) if r else None

    def usuarios(self) -> list[dict]:
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(select(users).order_by(users.c.nome, users.c.email)).mappings()]

    def atualizar_usuario(self, uid: int, **campos) -> None:
        if "senha" in campos:
            campos["senha_hash"] = security.hash_password(campos.pop("senha"))
        with self.engine.begin() as c:
            c.execute(update(users).where(users.c.id == uid).values(**campos))

    # ── Links ──
    def link(self, link_id: int) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(links).where(links.c.id == link_id)).mappings().first()
        return dict(r) if r else None

    def slug_existe(self, slug: str, exceto: int | None = None) -> bool:
        q = select(links.c.id).where(links.c.slug == slug)
        if exceto:
            q = q.where(links.c.id != exceto)
        with self.engine.connect() as c:
            return c.execute(q).first() is not None

    def criar_link(self, slug, destino, titulo, categoria, campanha, autor, **utms) -> dict:
        t = agora()
        with self.engine.begin() as c:
            row = c.execute(insert(links).values(
                slug=slug, destino=destino, titulo=titulo, categoria=categoria, campanha=campanha,
                **{k: utms.get(k, "") for k in UTMS},
                ativo=True, criado_por=autor, criado_em=t, atualizado_em=t,
            ).returning(*links.c)).mappings().one()
        row = dict(row)
        self._cache_put(row)
        return row

    def atualizar_link(self, link_id: int, **campos) -> dict:
        campos["atualizado_em"] = agora()
        with self.engine.begin() as c:
            row = c.execute(update(links).where(links.c.id == link_id).values(**campos)
                            .returning(*links.c)).mappings().one()
        row = dict(row)
        self._cache_put(row)
        return row

    def utms_usadas(self) -> dict[str, list[str]]:
        out = {}
        with self.engine.connect() as c:
            for k in UTMS:
                out[k] = [r[0] for r in c.execute(
                    select(links.c[k]).where(links.c[k] != "").distinct().order_by(links.c[k]).limit(200))]
        return out

    def campanhas(self) -> list[str]:
        with self.engine.connect() as c:
            return [r[0] for r in c.execute(
                select(links.c.campanha).where(links.c.campanha != "").distinct().order_by(links.c.campanha))]

    # ── Painel ──
    def painel(self) -> dict:
        """Todos os links + cliques (sem robôs): total, 7 dias e série dos últimos 14 dias."""
        d0 = hoje()
        ini14 = d0 - timedelta(days=13)
        with self.engine.connect() as c:
            rows = [dict(r) for r in c.execute(select(links).order_by(links.c.criado_em.desc())).mappings()]
            totais = dict(c.execute(
                select(clicks.c.link_id, func.count()).where(clicks.c.bot.is_(False)).group_by(clicks.c.link_id)
            ).all())
            serie = c.execute(
                select(clicks.c.link_id, clicks.c.dia, func.count())
                .where(and_(clicks.c.bot.is_(False), clicks.c.dia >= ini14))
                .group_by(clicks.c.link_id, clicks.c.dia)
            ).all()
        por_link: dict[int, dict[date, int]] = defaultdict(dict)
        for lid, dia, n in serie:
            por_link[lid][_d(dia)] = n
        dias = [ini14 + timedelta(days=i) for i in range(14)]
        geral = Counter()
        for r in rows:
            s = por_link.get(r["id"], {})
            r["serie14"] = [s.get(d, 0) for d in dias]
            r["cliques_7d"] = sum(r["serie14"][-7:])
            r["cliques_hoje"] = r["serie14"][-1]
            r["cliques_total"] = totais.get(r["id"], 0)
            for d, n in s.items():
                geral[d] += n
        return {
            "links": rows,
            "hoje": geral[d0],
            "ontem": geral[d0 - timedelta(days=1)],
            "sete": sum(geral[d] for d in dias[-7:]),
            "sete_antes": sum(geral[d] for d in dias[:7]),
            "serie": [{"dia": d.isoformat(), "n": geral[d]} for d in dias],
        }

    def estatisticas(self, link_id: int, dias: int | None) -> dict:
        """Tudo da página de um link. dias=None → desde a criação."""
        d0 = hoje()
        cond = [clicks.c.link_id == link_id]
        if dias:
            ini = d0 - timedelta(days=dias - 1)
            cond.append(clicks.c.dia >= ini)
        else:
            ini = None
        humano = and_(*cond, clicks.c.bot.is_(False))
        out: dict = {}
        with self.engine.connect() as c:
            out["total"] = c.execute(select(func.count()).where(humano)).scalar() or 0
            out["unicos"] = c.execute(select(func.count(func.distinct(clicks.c.visitante))).where(humano)).scalar() or 0
            out["bots"] = c.execute(select(func.count()).where(and_(*cond, clicks.c.bot.is_(True)))).scalar() or 0
            por_dia = dict((_d(k), v) for k, v in c.execute(
                select(clicks.c.dia, func.count()).where(humano).group_by(clicks.c.dia)).all())
            por_hora = dict(c.execute(select(clicks.c.hora, func.count()).where(humano).group_by(clicks.c.hora)).all())

            def top(col, limite=8):
                q = (select(col, func.count().label("n")).where(humano).group_by(col)
                     .order_by(func.count().desc()).limit(limite))
                return [{"nome": k or "—", "n": n} for k, n in c.execute(q).all()]

            out["origem"] = top(clicks.c.origem)
            out["dispositivo"] = top(clicks.c.dispositivo)
            out["sistema"] = top(clicks.c.sistema)
            out["navegador"] = top(clicks.c.navegador)
            out["pais"] = [x for x in top(clicks.c.pais) if x["nome"] != "—"]
            out["utm_source"] = [x for x in top(clicks.c.utm_source) if x["nome"] != "—"]
            out["utm_campaign"] = [x for x in top(clicks.c.utm_campaign) if x["nome"] != "—"]
            out["utm_medium"] = [x for x in top(clicks.c.utm_medium) if x["nome"] != "—"]
            out["utm_content"] = [x for x in top(clicks.c.utm_content) if x["nome"] != "—"]
            out["utm_term"] = [x for x in top(clicks.c.utm_term) if x["nome"] != "—"]
            out["ref"] = [x for x in top(clicks.c.ref, 50) if x["nome"] != "—"]
            out["sites"] = [x for x in top(clicks.c.referer_host) if x["nome"] != "—"]
            primeiro = c.execute(select(func.min(clicks.c.dia)).where(and_(clicks.c.link_id == link_id))).scalar()
        if ini is None:
            ini = _d(primeiro) if primeiro else d0
        n_dias = (d0 - ini).days + 1
        out["serie"] = [{"dia": (ini + timedelta(days=i)).isoformat(), "n": por_dia.get(ini + timedelta(days=i), 0)}
                        for i in range(n_dias)]
        out["horas"] = [por_hora.get(h, 0) for h in range(24)]
        out["media_dia"] = round(out["total"] / n_dias, 1) if n_dias else 0
        melhor = max(out["serie"], key=lambda x: x["n"]) if out["serie"] else None
        out["pico"] = melhor if melhor and melhor["n"] else None
        return out

    def exportar_cliques(self, link_id: int):
        cols = [clicks.c.ts, clicks.c.origem, clicks.c.referer_host, clicks.c.dispositivo, clicks.c.sistema,
                clicks.c.navegador, clicks.c.pais, clicks.c.utm_source, clicks.c.utm_medium,
                clicks.c.utm_campaign, clicks.c.utm_content, clicks.c.utm_term, clicks.c.ref, clicks.c.visitante, clicks.c.bot]
        with self.engine.connect() as c:
            res = c.execute(select(*cols).where(clicks.c.link_id == link_id).order_by(clicks.c.ts.desc()))
            yield [col.name for col in cols]
            for r in res:
                ts = r[0]
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                yield [ts.astimezone(TZ).strftime("%d/%m/%Y %H:%M:%S"), *r[1:-1], "sim" if r[-1] else "não"]


def _d(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])
