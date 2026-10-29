# Brabo Links

Encurtador de links com rastreio de cliques, feito para substituir o Bitly na Brabo Concursos.
Mesmo visual do Brabo Analytics e mesma stack do Radar de Projetos (FastAPI + Supabase + EasyPanel).

## O que faz
- **Encurtar**: cola o destino, escolhe o nome (`go.braboconcursos.com.br/aula-inss`). O nome é sugerido a partir do título ou do endereço, e o tipo (YouTube, página de vendas, checkout, e-book, WhatsApp) é detectado sozinho. O link já sai copiado.
- **Trocar o destino sem trocar o link**: dá para editar para onde ele aponta depois de publicado.
- **Rastrear**: cada clique guarda data/hora, origem (YouTube, Instagram, WhatsApp, Google, e-mail…), aparelho, sistema, navegador, site que mandou, UTMs e `?ref=` (identificador da pessoa). Robôs e pré-visualizações (WhatsApp, Facebook, Telegram…) são separados e **não entram na contagem**.
- **Pessoas diferentes**: hash anônimo de IP + navegador (o IP não é guardado).
- **QR Code** em PNG e SVG, **exportação CSV** de todos os cliques, **arquivar/reativar**.
- **Usuários** com papéis: Admin (tudo + usuários), Editor (cria/edita links), Só leitura.

## Como os links funcionam
- `GET /{nome}` → redireciona com **302 + `Cache-Control: no-store`** (o navegador nunca guarda o redirecionamento, então todo clique é contado e o destino pode mudar).
- Parâmetros do link curto seguem para o destino: `go…/aula-inss?utm_source=instagram` chega no YouTube com o `utm_source`.
- Nome inexistente ou arquivado → página “link não encontrado” com botão para o site.
- Os links são lidos de um cache em memória (atualizado a cada 30 s) e os cliques são gravados em lote a cada 2 s, então o redirecionamento não espera o banco.
- Nomes reservados: `app`, `api`, `login`, `logout`, `static`, `healthz` e afins.

## Rodar local
```bash
pip install -r requirements.txt
cp .env.example .env   # sem DATABASE_URL usa SQLite em data/links.db
python -m uvicorn app.main:app --reload
python -m pytest tests -q
```

## Deploy (EasyPanel)
1. **Banco**: Supabase comercial, pooler em modo transação (**porta 6543**; o modo sessão tem teto de 15 clientes somados entre todos os apps). Na primeira subida o app cria o schema `encurtador` com as tabelas `usuarios`, `links` e `cliques`, liga RLS e tira o acesso de `anon`/`authenticated`.
2. **App**: source GitHub, build pelo `Dockerfile`, porta 8000, **1 réplica** (cache e fila de cliques ficam em memória).
3. **Variáveis**: ver `.env.example` (`DATABASE_URL`, `PUBLIC_BASE`, `FALLBACK_URL`, `SESSION_SECRET`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`). `SESSION_SECRET` não pode mudar depois de no ar.
4. **Domínio**: registro A do subdomínio (ex.: `go`) na Hostinger apontando para o servidor do EasyPanel + domínio cadastrado no app com HTTPS.
5. Entrar com o admin inicial e trocar a senha (ícone de chave no menu).

## Estrutura
```
app/main.py       rotas (redirecionamento, painel, API)
app/store.py      banco: links, usuários, cliques em lote, estatísticas
app/tracking.py   origem, aparelho, robôs, hash do visitante
app/db.py         tabelas (Postgres em produção, SQLite local)
app/security.py   senha PBKDF2 e cookie de sessão assinado
app/templates/    telas (Jinja2)
app/static/       CSS com os tokens do Brabo Analytics, JS sem framework
tests/            testes ponta a ponta
```
