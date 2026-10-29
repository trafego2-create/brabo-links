"""Testes ponta a ponta com SQLite temporário: python -m pytest tests -q"""
import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp) / 'teste.db'}"
os.environ["PUBLIC_BASE"] = "https://go.exemplo.com"
os.environ["ADMIN_EMAIL"] = "admin@exemplo.com"
os.environ["ADMIN_PASSWORD"] = "senha-forte-123"
os.environ["SESSION_SECRET"] = "x" * 64

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main, tracking  # noqa: E402

CHROME_ANDROID = "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36"
INSTAGRAM = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Instagram 339.0.3.12.91"
WHATSAPP_PREVIEW = "WhatsApp/2.24.13.78 A"


@pytest.fixture(scope="module")
def c():
    with TestClient(main.app, base_url="https://go.exemplo.com") as cli:
        r = cli.post("/login", data={"email": "admin@exemplo.com", "senha": "senha-forte-123"}, follow_redirects=False)
        assert r.status_code == 303
        csrf = main.security.read_session(cli.cookies.get(main.COOKIE))["csrf"]
        cli.headers["X-CSRF"] = csrf
        yield cli


def test_login_errado():
    with TestClient(main.app) as cli:
        r = cli.post("/login", data={"email": "admin@exemplo.com", "senha": "errada"})
        assert "incorretos" in r.text
        assert cli.get("/app", follow_redirects=False).status_code == 303


def test_criar_e_redirecionar(c):
    r = c.post("/api/links", json={"destino": "youtube.com/watch?v=abc", "slug": "Aula-INSS", "titulo": "Aula 1"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["slug"] == "aula-inss" and j["categoria"] == "youtube"
    assert j["curto"] == "https://go.exemplo.com/aula-inss"
    assert j["destino"] == "https://youtube.com/watch?v=abc"

    r = c.get("/AULA-inss?utm_source=instagram&ref=joao@x.com", follow_redirects=False,
              headers={"user-agent": CHROME_ANDROID})
    assert r.status_code == 302
    assert r.headers["location"] == "https://youtube.com/watch?v=abc&utm_source=instagram&ref=joao%40x.com"
    assert r.headers["cache-control"] == "no-store"


def test_slug_repetido_e_reservado(c):
    assert c.post("/api/links", json={"destino": "https://a.com", "slug": "aula-inss"}).status_code == 409
    assert c.post("/api/links", json={"destino": "https://a.com", "slug": "app"}).status_code == 400
    assert c.post("/api/links", json={"destino": "https://a.com", "slug": "com espaço"}).status_code == 400
    assert c.post("/api/links", json={"destino": "nada", "slug": "ok"}).status_code == 400
    assert c.post("/api/links", json={"destino": "https://go.exemplo.com/aula-inss", "slug": "loop"}).status_code == 400


def test_csrf_obrigatorio(c):
    r = c.post("/api/links", json={"destino": "https://a.com", "slug": "sem-csrf"}, headers={"X-CSRF": "errado"})
    assert r.status_code == 403


def test_cliques_e_estatisticas(c):
    lid = c.post("/api/links", json={"destino": "https://braboconcursos.com.br/ebook", "slug": "ebook-tj"}).json()["id"]
    c.get("/ebook-tj", headers={"user-agent": INSTAGRAM}, follow_redirects=False)
    c.get("/ebook-tj", headers={"user-agent": INSTAGRAM}, follow_redirects=False)  # mesma pessoa
    c.get("/ebook-tj", headers={"user-agent": CHROME_ANDROID, "referer": "https://www.youtube.com/"}, follow_redirects=False)
    c.get("/ebook-tj", headers={"user-agent": WHATSAPP_PREVIEW}, follow_redirects=False)  # robô
    c.head("/ebook-tj", headers={"user-agent": CHROME_ANDROID})  # HEAD não conta
    main.store.gravar_cliques()

    st = main.store.estatisticas(lid, 30)
    assert st["total"] == 3
    assert st["unicos"] == 2
    assert st["bots"] == 1
    origens = {o["nome"]: o["n"] for o in st["origem"]}
    assert origens == {"Instagram": 2, "YouTube": 1}

    painel = c.get("/app")
    assert painel.status_code == 200 and "ebook-tj" in painel.text
    pag = c.get(f"/app/links/{lid}?periodo=7")
    assert pag.status_code == 200 and "Robôs descartados" in pag.text
    csv = c.get(f"/app/links/{lid}/cliques.csv")
    assert csv.status_code == 200 and csv.text.count("\n") == 5  # cabeçalho + 4 cliques


def test_editar_destino_mantem_link(c):
    lid = c.post("/api/links", json={"destino": "https://a.com", "slug": "troca"}).json()["id"]
    r = c.patch(f"/api/links/{lid}", json={"destino": "https://b.com/novo", "slug": "troca", "titulo": "T"})
    assert r.status_code == 200
    assert c.get("/troca", follow_redirects=False).headers["location"] == "https://b.com/novo"


def test_arquivado_e_inexistente(c):
    lid = c.post("/api/links", json={"destino": "https://a.com", "slug": "velho"}).json()["id"]
    c.post(f"/api/links/{lid}/ativo", json={"ativo": False})
    assert c.get("/velho", follow_redirects=False).status_code == 404
    assert c.get("/nao-existe", follow_redirects=False).status_code == 404


def test_qr(c):
    lid = c.post("/api/links", json={"destino": "https://a.com", "slug": "qr-teste"}).json()["id"]
    assert c.get(f"/app/links/{lid}/qr.svg").text.lstrip().startswith("<svg")
    assert c.get(f"/app/links/{lid}/qr.png").content[:4] == b"\x89PNG"


def test_usuarios_e_permissoes(c):
    assert c.post("/api/usuarios", json={"email": "leitor@x.com", "nome": "L", "senha": "12345678", "papel": "leitura"}).status_code == 200
    with TestClient(main.app, base_url="https://go.exemplo.com") as outro:
        outro.post("/login", data={"email": "leitor@x.com", "senha": "12345678"})
        outro.headers["X-CSRF"] = main.security.read_session(outro.cookies.get(main.COOKIE))["csrf"]
        assert outro.get("/app").status_code == 200
        assert outro.post("/api/links", json={"destino": "https://a.com", "slug": "nao"}).status_code == 403
        assert outro.get("/app/usuarios", follow_redirects=False).status_code == 303


def test_sugestao(c):
    r = c.get("/api/sugestao", params={"destino": "https://pay.hotmart.com/X123", "slug": "aula-inss"}).json()
    assert r["categoria"] == "checkout"
    assert r["slug_erro"] == "Esse nome já está em uso."


def test_tracking_basico():
    assert tracking.is_bot("facebookexternalhit/1.1")
    assert not tracking.is_bot(CHROME_ANDROID)
    assert tracking.dispositivo(INSTAGRAM) == ("Celular", "iOS", "Instagram (app)")
    assert tracking.origem("", "https://m.youtube.com/watch", "")[0] == "YouTube"
    assert tracking.origem("", "https://www.google.com.br/", "")[0] == "Google"
    assert tracking.origem("", "", "wpp")[0] == "WhatsApp"


def test_utms_fixas_no_padrao_do_active(c):
    camp = "[MA][cadastro][captação][quente][principal][PES-NOV-26][26.10.26]"
    r = c.post("/api/links", json={
        "destino": "https://braboconcursos.com.br/tj?x=1", "slug": "tj-bio",
        "utm_source": "ig", "utm_medium": "social", "utm_campaign": camp,
        "utm_content": "link_in_bio", "utm_term": "AD393 - Carrossel Edital aberto - PES-NOV-26"})
    assert r.status_code == 200, r.text
    assert r.json()["final"].startswith("https://braboconcursos.com.br/tj?x=1&utm_source=ig&utm_medium=social&utm_campaign=")

    loc = c.get("/tj-bio", follow_redirects=False, headers={"user-agent": CHROME_ANDROID}).headers["location"]
    from urllib.parse import parse_qs, urlsplit
    q = parse_qs(urlsplit(loc).query)
    assert q["utm_campaign"] == [camp] and q["utm_term"] == ["AD393 - Carrossel Edital aberto - PES-NOV-26"]
    assert q["x"] == ["1"]

    # UTM no clique vence a fixa; as outras fixas continuam
    loc = c.get("/tj-bio?utm_source=whatsapp", follow_redirects=False, headers={"user-agent": CHROME_ANDROID}).headers["location"]
    q = parse_qs(urlsplit(loc).query)
    assert q["utm_source"] == ["whatsapp"] and q["utm_medium"] == ["social"]

    main.store.gravar_cliques()
    lid = r.json()["id"]
    st = main.store.estatisticas(lid, 7)
    assert {x["nome"] for x in st["utm_source"]} == {"ig", "whatsapp"}
    assert st["utm_term"][0]["n"] == 2
    assert "AD393" in c.get(f"/app/links/{lid}").text


def test_utm_source_sem_medium(c):
    r = c.post("/api/links", json={"destino": "https://a.com", "slug": "so-source", "utm_source": "ig"})
    assert r.status_code == 400 and "utm_medium" in r.json()["erro"]


def test_cache_incremental_pega_edicao_feita_fora(c):
    """Link criado/editado pelo Brabo Analytics (direto no banco) chega no cache
    pelo atualizar_cache, sem reler tudo."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import insert, update

    from app.db import links

    st = main.store
    st.recarregar_cache()
    depois = datetime.now(timezone.utc) + timedelta(seconds=1)
    with st.engine.begin() as cx:
        cx.execute(insert(links).values(slug="feito-no-analytics", destino="https://exemplo.com/a", titulo="x",
                                        criado_em=depois, atualizado_em=depois))
    assert st.atualizar_cache() == 1
    assert st._cache["feito-no-analytics"]["destino"] == "https://exemplo.com/a"
    depois2 = depois + timedelta(seconds=1)
    with st.engine.begin() as cx:
        cx.execute(update(links).where(links.c.slug == "feito-no-analytics")
                   .values(slug="renomeado-no-analytics", ativo=False, atualizado_em=depois2))
    assert st.atualizar_cache() == 1
    assert "feito-no-analytics" not in st._cache
    assert st._cache["renomeado-no-analytics"]["ativo"] is False
    assert st.atualizar_cache() == 0


def test_painel_aposentado_vai_pro_analytics(c, monkeypatch):
    monkeypatch.setattr(main.config, "PAINEL_URL", "https://analytics.exemplo.com")
    r = c.get("/app/links/7", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://analytics.exemplo.com/links/7"
    assert c.get("/login", follow_redirects=False).headers["location"] == "https://analytics.exemplo.com/links"
    assert c.post("/api/links", json={}).status_code == 410
    # link curto continua redirecionando
    assert c.get("/aula-inss", follow_redirects=False).status_code == 302
