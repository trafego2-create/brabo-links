"""Brabo Links — encurtador de links com rastreio de cliques (substituto do Bitly).

Rotas:
  /{slug}            redireciona e registra o clique (público)
  /app ...           painel (login)
  /api ...           ações do painel (JSON, com CSRF)
"""
import asyncio
import csv
import io
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import segno
from anyio import to_thread
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError

from . import config, security, tracking
from .db import make_engine
from .store import CATEGORIAS, PAPEIS, TZ, Store, detecta_categoria, normaliza_destino, normaliza_slug, \
    slugify, valida_destino, valida_slug

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("links")

BASE = Path(__file__).resolve().parent
COOKIE = "bl_sessao"
store: Store | None = None


async def _loop_gravacao() -> None:
    while True:
        await asyncio.sleep(config.FLUSH_SECONDS)
        try:
            await to_thread.run_sync(store.gravar_cliques)
        except Exception:
            log.exception("loop de gravação")


async def _loop_cache() -> None:
    while True:
        await asyncio.sleep(config.CACHE_REFRESH_SECONDS)
        try:
            await to_thread.run_sync(store.recarregar_cache)
        except Exception:
            log.exception("refresh do cache")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store
    if store is None:
        store = Store(make_engine())
    for tentativa in range(6):
        try:
            await to_thread.run_sync(store.init)
            break
        except Exception:
            if tentativa == 5:
                raise
            log.exception("banco indisponível na subida, tentando de novo")
            await asyncio.sleep(5)
    tarefas = [asyncio.create_task(_loop_gravacao()), asyncio.create_task(_loop_cache())]
    yield
    for t in tarefas:
        t.cancel()
    await to_thread.run_sync(store.gravar_cliques)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


def _num(v) -> str:
    try:
        return f"{int(v):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(v)


def _dt(v) -> str:
    if not v:
        return ""
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(TZ).strftime("%d/%m/%Y %H:%M")


templates.env.filters["num"] = _num
templates.env.filters["dt"] = _dt
templates.env.globals.update(
    CATEGORIAS=CATEGORIAS, PAPEIS=PAPEIS,
    BASE_CURTA=config.PUBLIC_BASE, HOST_CURTO=config.PUBLIC_BASE.split("://", 1)[-1],
)


# ── Sessão ────────────────────────────────────────────────────────────────────

def sessao(req: Request) -> dict | None:
    return security.read_session(req.cookies.get(COOKIE))


def usuario_atual(req: Request) -> dict | None:
    s = sessao(req)
    if not s:
        return None
    u = store.usuario(s["u"])
    if not u or not u["ativo"]:
        return None
    u["csrf"] = s["csrf"]
    return u


def exige(req: Request, *papeis: str) -> dict:
    u = usuario_atual(req)
    if not u:
        raise HTTPException(401, "Sessão expirada. Entre de novo.")
    if papeis and u["papel"] not in papeis:
        raise HTTPException(403, "Seu usuário não tem permissão para isso.")
    if req.method not in ("GET", "HEAD") and req.headers.get("x-csrf") != u["csrf"]:
        raise HTTPException(403, "Recarregue a página e tente de novo.")
    return u


def pagina(req: Request) -> dict | RedirectResponse:
    u = usuario_atual(req)
    if not u:
        return RedirectResponse(f"/login?volta={req.url.path}", status_code=303)
    return u


def render(req: Request, nome: str, u: dict | None, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(req, nome, {"u": u, **ctx})


@app.exception_handler(HTTPException)
async def _erro(req: Request, exc: HTTPException):
    if req.url.path.startswith("/api/"):
        return JSONResponse({"erro": exc.detail}, status_code=exc.status_code)
    if exc.status_code == 401:
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(f"<p>{exc.detail}</p>", status_code=exc.status_code)


# ── Login ─────────────────────────────────────────────────────────────────────

_tentativas: dict[str, deque] = defaultdict(deque)


@app.get("/login")
async def login_form(req: Request, volta: str = "/app"):
    if usuario_atual(req):
        return RedirectResponse("/app", status_code=303)
    return render(req, "login.html", None, volta=volta, erro="")


@app.post("/login")
async def login(req: Request):
    form = await req.form()
    email = str(form.get("email", "")).strip().lower()
    senha = str(form.get("senha", ""))
    volta = str(form.get("volta") or "/app")
    if not volta.startswith("/") or volta.startswith("//"):
        volta = "/app"
    ip = tracking.client_ip(req.headers, req.client.host if req.client else "")
    fila = _tentativas[ip]
    agora = time.time()
    while fila and agora - fila[0] > 600:
        fila.popleft()
    if len(fila) >= 10:
        return render(req, "login.html", None, volta=volta, erro="Muitas tentativas. Espere alguns minutos.", email=email)
    u = await to_thread.run_sync(store.usuario_por_email, email)
    if not u or not u["ativo"] or not security.check_password(senha, u["senha_hash"]):
        fila.append(agora)
        return render(req, "login.html", None, volta=volta, erro="E-mail ou senha incorretos.", email=email)
    resp = RedirectResponse(volta, status_code=303)
    resp.set_cookie(COOKIE, security.make_session(u["id"]), max_age=config.SESSION_DAYS * 86400,
                    httponly=True, samesite="lax", secure=config.COOKIE_SECURE)
    return resp


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


# ── Páginas ───────────────────────────────────────────────────────────────────

@app.get("/")
async def raiz(req: Request):
    return RedirectResponse("/app" if usuario_atual(req) else config.FALLBACK_URL, status_code=302)


@app.get("/app")
async def painel(req: Request):
    u = pagina(req)
    if isinstance(u, RedirectResponse):
        return u
    dados = await to_thread.run_sync(store.painel)
    camps = await to_thread.run_sync(store.campanhas)
    return render(req, "painel.html", u, d=dados, campanhas=camps, nav="links")


PERIODOS = {"7": 7, "30": 30, "90": 90, "tudo": None}


@app.get("/app/links/{link_id}")
async def pagina_link(req: Request, link_id: int, periodo: str = "30"):
    u = pagina(req)
    if isinstance(u, RedirectResponse):
        return u
    link = await to_thread.run_sync(store.link, link_id)
    if not link:
        raise HTTPException(404, "Link não encontrado.")
    if periodo not in PERIODOS:
        periodo = "30"
    st = await to_thread.run_sync(store.estatisticas, link_id, PERIODOS[periodo])
    camps = await to_thread.run_sync(store.campanhas)
    return render(req, "link.html", u, link=link, st=st, periodo=periodo, campanhas=camps, nav="links")


@app.get("/app/links/{link_id}/cliques.csv")
async def csv_link(req: Request, link_id: int):
    exige(req)
    link = await to_thread.run_sync(store.link, link_id)
    if not link:
        raise HTTPException(404, "Link não encontrado.")

    def gerar():
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        buf.write("﻿")  # Excel abre com acento certo
        for linha in store.exportar_cliques(link_id):
            w.writerow(linha)
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()

    nome = f"cliques-{link['slug']}-{datetime.now(TZ):%Y%m%d}.csv"
    return StreamingResponse(gerar(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{nome}"'})


@app.get("/app/links/{link_id}/qr.{fmt}")
async def qr(req: Request, link_id: int, fmt: str, baixar: int = 0):
    exige(req)
    if fmt not in ("svg", "png"):
        raise HTTPException(404)
    link = await to_thread.run_sync(store.link, link_id)
    if not link:
        raise HTTPException(404, "Link não encontrado.")
    code = segno.make(f"{config.PUBLIC_BASE}/{link['slug']}", error="m")
    buf = io.BytesIO()
    if fmt == "svg":
        code.save(buf, kind="svg", scale=10, border=2, dark="#111111", light="#ffffff", xmldecl=False)
        mt = "image/svg+xml"
    else:
        code.save(buf, kind="png", scale=16, border=2, dark="#111111", light="#ffffff")
        mt = "image/png"
    headers = {"Cache-Control": "private, max-age=300"}
    if baixar:
        headers["Content-Disposition"] = f'attachment; filename="qr-{link["slug"]}.{fmt}"'
    return Response(buf.getvalue(), media_type=mt, headers=headers)


@app.get("/app/usuarios")
async def pagina_usuarios(req: Request):
    u = pagina(req)
    if isinstance(u, RedirectResponse):
        return u
    if u["papel"] != "admin":
        return RedirectResponse("/app", status_code=303)
    lista = await to_thread.run_sync(store.usuarios)
    return render(req, "usuarios.html", u, usuarios=lista, nav="usuarios")


# ── API do painel ─────────────────────────────────────────────────────────────

def _campos_link(body: dict, link_id: int | None = None) -> dict:
    destino = normaliza_destino(str(body.get("destino", "")))
    erro = valida_destino(destino)
    if erro:
        raise HTTPException(400, erro)
    slug = normaliza_slug(str(body.get("slug", "")))
    erro = valida_slug(slug)
    if erro:
        raise HTTPException(400, erro)
    if store.slug_existe(slug, exceto=link_id):
        raise HTTPException(409, f"Já existe um link “{slug}”. Escolha outro nome.")
    categoria = str(body.get("categoria") or "") or detecta_categoria(destino)
    if categoria not in CATEGORIAS:
        categoria = "pagina"
    return {
        "slug": slug, "destino": destino, "categoria": categoria,
        "titulo": str(body.get("titulo", "")).strip()[:200] or slug,
        "campanha": str(body.get("campanha", "")).strip().upper()[:60],
    }


def _link_json(r: dict) -> dict:
    return {**{k: r[k] for k in ("id", "slug", "destino", "titulo", "categoria", "campanha", "ativo")},
            "curto": f"{config.PUBLIC_BASE}/{r['slug']}"}


@app.get("/api/sugestao")
async def sugestao(req: Request, destino: str = "", titulo: str = "", slug: str = "", id: int = 0):
    """Categoria detectada + nome sugerido + se o nome está livre (enquanto a pessoa digita)."""
    exige(req)
    destino = normaliza_destino(destino)
    base = slugify(titulo)
    if not base and destino:
        partes = [p for p in urlsplit(destino).path.split("/") if p]
        base = slugify(partes[-1].rsplit(".", 1)[0]) if partes else ""
    if base and (valida_slug(base) or await to_thread.run_sync(store.slug_existe, base)):
        # já usado ou reservado: tenta base-2, base-3…
        for i in range(2, 50):
            cand = f"{base[:56]}-{i}"
            if not valida_slug(cand) and not await to_thread.run_sync(store.slug_existe, cand):
                base = cand
                break
    out = {"categoria": detecta_categoria(destino) if destino else "", "sugestao": base}
    s = normaliza_slug(slug)
    if s:
        erro = valida_slug(s)
        if not erro and await to_thread.run_sync(lambda: store.slug_existe(s, exceto=id or None)):
            erro = "Esse nome já está em uso."
        out["slug_erro"] = erro or ""
    return out


@app.post("/api/links")
async def criar_link(req: Request):
    u = exige(req, "admin", "editor")
    body = await req.json()
    campos = await to_thread.run_sync(_campos_link, body)
    try:
        r = await to_thread.run_sync(lambda: store.criar_link(**campos, autor=u["email"]))
    except IntegrityError:
        raise HTTPException(409, "Esse nome acabou de ser usado por outra pessoa. Escolha outro.")
    return _link_json(r)


@app.patch("/api/links/{link_id}")
async def editar_link(req: Request, link_id: int):
    exige(req, "admin", "editor")
    if not await to_thread.run_sync(store.link, link_id):
        raise HTTPException(404, "Link não encontrado.")
    body = await req.json()
    campos = await to_thread.run_sync(_campos_link, body, link_id)
    try:
        r = await to_thread.run_sync(lambda: store.atualizar_link(link_id, **campos))
    except IntegrityError:
        raise HTTPException(409, "Esse nome já está em uso.")
    return _link_json(r)


@app.post("/api/links/{link_id}/ativo")
async def ativar_link(req: Request, link_id: int):
    exige(req, "admin", "editor")
    body = await req.json()
    if not await to_thread.run_sync(store.link, link_id):
        raise HTTPException(404, "Link não encontrado.")
    r = await to_thread.run_sync(lambda: store.atualizar_link(link_id, ativo=bool(body.get("ativo"))))
    return _link_json(r)


@app.post("/api/usuarios")
async def criar_usuario(req: Request):
    exige(req, "admin")
    b = await req.json()
    email = str(b.get("email", "")).strip().lower()
    senha = str(b.get("senha", ""))
    papel = str(b.get("papel", "editor"))
    if "@" not in email:
        raise HTTPException(400, "E-mail inválido.")
    if len(senha) < 8:
        raise HTTPException(400, "A senha precisa ter pelo menos 8 caracteres.")
    if papel not in PAPEIS:
        raise HTTPException(400, "Papel inválido.")
    if await to_thread.run_sync(store.usuario_por_email, email):
        raise HTTPException(409, "Já existe um usuário com esse e-mail.")
    uid = await to_thread.run_sync(store.criar_usuario, email, str(b.get("nome", "")), senha, papel)
    return {"id": uid}


@app.patch("/api/usuarios/{uid}")
async def editar_usuario(req: Request, uid: int):
    eu = exige(req, "admin")
    b = await req.json()
    alvo = await to_thread.run_sync(store.usuario, uid)
    if not alvo:
        raise HTTPException(404, "Usuário não encontrado.")
    campos = {}
    if "nome" in b:
        campos["nome"] = str(b["nome"]).strip()[:120]
    if "papel" in b:
        if b["papel"] not in PAPEIS:
            raise HTTPException(400, "Papel inválido.")
        campos["papel"] = b["papel"]
    if "ativo" in b:
        campos["ativo"] = bool(b["ativo"])
    if b.get("senha"):
        if len(b["senha"]) < 8:
            raise HTTPException(400, "A senha precisa ter pelo menos 8 caracteres.")
        campos["senha"] = b["senha"]
    if uid == eu["id"] and (campos.get("ativo") is False or campos.get("papel", "admin") != "admin"):
        raise HTTPException(400, "Você não pode tirar o seu próprio acesso de admin.")
    if campos:
        await to_thread.run_sync(lambda: store.atualizar_usuario(uid, **campos))
    return {"ok": True}


@app.post("/api/conta/senha")
async def trocar_senha(req: Request):
    u = exige(req)
    b = await req.json()
    if not security.check_password(str(b.get("atual", "")), u["senha_hash"]):
        raise HTTPException(400, "Senha atual incorreta.")
    nova = str(b.get("nova", ""))
    if len(nova) < 8:
        raise HTTPException(400, "A nova senha precisa ter pelo menos 8 caracteres.")
    await to_thread.run_sync(lambda: store.atualizar_usuario(u["id"], senha=nova))
    return {"ok": True}


# ── Público ───────────────────────────────────────────────────────────────────

@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/robots.txt")
async def robots():
    return Response("User-agent: *\nDisallow: /\n", media_type="text/plain")


@app.get("/favicon.ico")
async def favicon():
    return RedirectResponse("/static/img/favicon-brabo-concursos.png", status_code=301)


def _junta_query(destino: str, extra: list[tuple[str, str]]) -> str:
    if not extra:
        return destino
    p = urlsplit(destino)
    chaves = {k for k, _ in extra}
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k not in chaves] + extra
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))


@app.api_route("/{slug}", methods=["GET", "HEAD"])
async def redirecionar(req: Request, slug: str):
    slug = normaliza_slug(slug)
    alvo = store.resolver(slug) if slug else None
    if not alvo or not alvo["ativo"]:
        return templates.TemplateResponse(req, "nao_encontrado.html", {"fallback": config.FALLBACK_URL},
                                          status_code=404)
    extra = list(req.query_params.multi_items())
    destino = _junta_query(alvo["destino"], extra)

    if req.method == "GET":
        ua = req.headers.get("user-agent", "")[:500]
        q = req.query_params
        utm_source = q.get("utm_source", "")[:100]
        origem, host = tracking.origem(ua, req.headers.get("referer", ""), utm_source)
        tipo, so, nav = tracking.dispositivo(ua)
        ip = tracking.client_ip(req.headers, req.client.host if req.client else "")
        t = datetime.now(timezone.utc)
        local = t.astimezone(TZ)
        store.registrar({
            "link_id": alvo["id"], "ts": t, "dia": local.date(), "hora": local.hour,
            "visitante": tracking.visitante(ip, ua), "bot": tracking.is_bot(ua),
            "origem": origem[:30], "referer_host": host[:120],
            "dispositivo": tipo, "sistema": so, "navegador": nav,
            "pais": (req.headers.get("cf-ipcountry", "") or "")[:2].upper().replace("XX", ""),
            "utm_source": utm_source, "utm_medium": q.get("utm_medium", "")[:100],
            "utm_campaign": q.get("utm_campaign", "")[:150], "utm_content": q.get("utm_content", "")[:150],
            "ref": (q.get("ref") or q.get("lead") or "")[:120],
        })
    # 302 + no-store: o navegador não guarda o redirecionamento, então todo clique passa por aqui
    # e o destino pode ser trocado depois sem quebrar o link.
    return RedirectResponse(destino, status_code=302, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})
