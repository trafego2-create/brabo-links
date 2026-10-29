/* Brabo Links — comportamento do painel (sem framework). */
(function () {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (n) => Number(n || 0).toLocaleString('pt-BR');
  const host = BL.base.replace(/^https?:\/\//, '');

  async function api(method, url, body) {
    const r = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json', 'X-CSRF': BL.csrf },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'same-origin',
    });
    let data = {};
    try { data = await r.json(); } catch (e) {}
    if (!r.ok) throw new Error(data.erro || 'Algo deu errado. Tente de novo.');
    return data;
  }

  function toast(msg, err) {
    const t = document.createElement('div');
    t.className = 'toast' + (err ? ' err' : '');
    t.innerHTML = `<i class="ti ti-${err ? 'alert-circle' : 'circle-check'}"></i>${esc(msg)}`;
    $('#toasts').appendChild(t);
    setTimeout(() => t.remove(), err ? 5000 : 2600);
  }

  async function copiar(txt, silencioso) {
    try {
      await navigator.clipboard.writeText(txt);
    } catch (e) {
      const ta = document.createElement('textarea');
      ta.value = txt; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); } catch (e2) {}
      ta.remove();
    }
    if (!silencioso) toast('Link copiado!');
  }

  function abrir(id) { const m = document.getElementById(id); m.classList.add('on'); const f = $('input:not([type=hidden]), select', m); if (f) setTimeout(() => f.focus(), 30); }
  function fechar(el) { (el.closest ? el.closest('.modal-bg') : el).classList.remove('on'); }
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') $$('.modal-bg.on').forEach((m) => m.classList.remove('on')); });

  function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
  function linkDe(el) { return JSON.parse(el.closest('[data-link]').dataset.link); }

  // ── Tema ────────────────────────────────────────────────────────────────
  function rotuloTema() {
    const lbl = $('#bl-tema-lbl');
    if (lbl) lbl.textContent = document.documentElement.getAttribute('data-bs-theme') === 'brabo' ? 'Tema claro' : 'Tema escuro';
  }
  window.blTema = function () {
    const escuro = document.documentElement.getAttribute('data-bs-theme') !== 'brabo';
    if (escuro) document.documentElement.setAttribute('data-bs-theme', 'brabo');
    else document.documentElement.removeAttribute('data-bs-theme');
    try { localStorage.setItem('bl-tema', escuro ? 'brabo' : 'claro'); } catch (e) {}
    rotuloTema();
  };
  rotuloTema();

  window.blCopiar = copiar;
  window.blAbrir = abrir;
  window.blFechar = fechar;

  window.blTrocarSenha = async function (e) {
    e.preventDefault();
    const f = e.target;
    try {
      await api('POST', '/api/conta/senha', { atual: f.atual.value, nova: f.nova.value });
      f.reset(); fechar(f); toast('Senha trocada.');
    } catch (err) { toast(err.message, true); }
  };

  // ── QR ──────────────────────────────────────────────────────────────────
  window.blQR = function (el) {
    const l = el.id !== undefined && el.slug ? el : linkDe(el);
    const base = `/app/links/${l.id}/qr`;
    $('#qr-img').src = `${base}.svg`;
    $('#qr-url').textContent = `${host}/${l.slug}`;
    $('#qr-png').href = `${base}.png?baixar=1`;
    $('#qr-svg').href = `${base}.svg?baixar=1`;
    abrir('m-qr');
  };

  // ── Editar ──────────────────────────────────────────────────────────────
  let editando = null;
  window.blEditar = function (el) {
    editando = linkDe(el);
    const f = $('#m-editar form');
    f.id.value = editando.id;
    f.destino.value = editando.destino;
    f.slug.value = editando.slug;
    f.titulo.value = editando.titulo;
    f.categoria.value = editando.categoria;
    f.campanha.value = editando.campanha;
    $('#warn-slug').classList.remove('on');
    $('[data-err=slug]', f).textContent = '';
    $('#btn-arquivar').innerHTML = editando.ativo
      ? '<i class="ti ti-archive"></i> Arquivar'
      : '<i class="ti ti-archive-off"></i> Reativar';
    f.slug.oninput = () => {
      f.slug.value = f.slug.value.toLowerCase().replace(/\s+/g, '-');
      $('#warn-slug').classList.toggle('on', f.slug.value !== editando.slug);
    };
    abrir('m-editar');
  };

  window.blSalvarEdicao = async function (e) {
    e.preventDefault();
    const f = e.target;
    try {
      await api('PATCH', `/api/links/${f.id.value}`, {
        destino: f.destino.value, slug: f.slug.value, titulo: f.titulo.value,
        categoria: f.categoria.value, campanha: f.campanha.value,
      });
      toast('Link salvo.');
      setTimeout(() => location.reload(), 400);
    } catch (err) { $('[data-err=slug]', f).textContent = ''; toast(err.message, true); }
  };

  window.blArquivar = async function () {
    if (!editando) return;
    const ativo = !editando.ativo;
    if (!ativo && !confirm(`Arquivar ${host}/${editando.slug}?\n\nQuem clicar vai ver a página de “link não encontrado”. Dá pra reativar depois.`)) return;
    try {
      await api('POST', `/api/links/${editando.id}/ativo`, { ativo });
      toast(ativo ? 'Link reativado.' : 'Link arquivado.');
      setTimeout(() => location.reload(), 400);
    } catch (err) { toast(err.message, true); }
  };

  // ── Painel: criar + filtrar ─────────────────────────────────────────────
  const CATS = {
    youtube: ['YouTube', 'ti-brand-youtube'], vendas: ['Página de vendas', 'ti-shopping-cart'],
    checkout: ['Checkout', 'ti-credit-card'], ebook: ['E-book / Material', 'ti-book'],
    whatsapp: ['WhatsApp', 'ti-brand-whatsapp'], pagina: ['Outro', 'ti-link'],
  };

  function linhaNova(l) {
    const url = `${BL.base}/${l.slug}`;
    const div = document.createElement('div');
    div.className = 'lrow';
    Object.assign(div.dataset, {
      id: l.id, cat: l.categoria, camp: l.campanha, ativo: '1', c7: '0', ct: '0',
      criado: new Date().toISOString(), q: `${l.titulo} ${l.slug} ${l.destino} ${l.campanha}`.toLowerCase(),
      link: JSON.stringify(l),
    });
    const vazio = Array.from({ length: 14 }, (_, i) => `<rect x="${(i * 10.7).toFixed(1)}" y="32.5" width="8.7" height="1.5" rx="2" fill="var(--bs-border)"></rect>`).join('');
    div.innerHTML = `
      <div class="cat-ico cat-${esc(l.categoria)}" title="${esc(CATS[l.categoria][0])}"><i class="ti ${CATS[l.categoria][1]}"></i></div>
      <div class="lmain">
        <a class="ltitle" href="/app/links/${l.id}">${esc(l.titulo)} ${l.campanha ? `<span class="tag camp">${esc(l.campanha)}</span>` : ''}<span class="tag" style="background:var(--bs-success-bg);color:var(--bs-success);border-color:transparent">novo</span></a>
        <div class="lshort"><a href="${esc(url)}" target="_blank" rel="noopener">${esc(host)}/${esc(l.slug)}</a>
          <button class="icon-btn" style="width:26px;height:26px;font-size:15px" title="Copiar link" data-copiar="${esc(url)}"><i class="ti ti-copy"></i></button></div>
        <div class="ldest" title="${esc(l.destino)}"><i class="ti ti-corner-down-right"></i> ${esc(l.destino)}</div>
      </div>
      <div class="lspark"><svg viewBox="0 0 150 34">${vazio}</svg><small>últimos 14 dias</small></div>
      <div class="lnum"><b>0</b><small>7 dias</small></div>
      <div class="lacts">
        <button class="icon-btn opt" title="QR Code" onclick="blQR(this)"><i class="ti ti-qrcode"></i></button>
        <a class="icon-btn opt" title="Estatísticas" href="/app/links/${l.id}"><i class="ti ti-chart-bar"></i></a>
        <button class="icon-btn opt" title="Editar" onclick="blEditar(this)"><i class="ti ti-pencil"></i></button>
      </div>`;
    $$('[data-copiar]', div).forEach((b) => b.addEventListener('click', () => copiar(b.dataset.copiar)));
    return div;
  }

  let catAtual = '';
  window.blFiltrar = function () {
    const q = ($('#f-busca')?.value || '').trim().toLowerCase();
    const camp = $('#f-camp')?.value || '';
    const status = $('#f-status')?.value ?? '1';
    const ordem = $('#f-ordem')?.value || 'criado';
    const lista = $('#lista');
    const linhas = $$('.lrow', lista);
    let visiveis = 0;
    linhas.forEach((r) => {
      const ok = (!q || r.dataset.q.includes(q)) && (!catAtual || r.dataset.cat === catAtual)
        && (!camp || r.dataset.camp === camp) && (status === '' || r.dataset.ativo === status);
      r.style.display = ok ? '' : 'none';
      if (ok) visiveis++;
    });
    const chave = { criado: (r) => r.dataset.criado, c7: (r) => +r.dataset.c7, ct: (r) => +r.dataset.ct }[ordem];
    linhas.sort((a, b) => (chave(a) < chave(b) ? 1 : chave(a) > chave(b) ? -1 : 0)).forEach((r) => lista.appendChild(r));
    const vz = $('#vazio');
    vz.style.display = visiveis ? 'none' : '';
    $('#vazio-txt').textContent = linhas.length ? 'Nenhum link encontrado com esses filtros.' : 'Nenhum link ainda. Crie o primeiro aí em cima.';
  };

  window.blInitPainel = function () {
    $$('#f-cats .chip').forEach((c) => c.addEventListener('click', () => {
      $$('#f-cats .chip').forEach((x) => x.classList.remove('on'));
      c.classList.add('on');
      catAtual = c.dataset.cat;
      blFiltrar();
    }));
    blFiltrar();

    const form = $('#f-criar');
    if (!form) return;
    const slug = $('#c-slug'), destino = $('#c-destino'), titulo = $('#c-titulo'), cat = $('#c-cat');
    const msg = $('#c-slug-msg'), box = $('#c-slug-box');
    const msgPadrao = msg.innerHTML;
    let slugMexido = false;

    const sugerir = debounce(async () => {
      const p = new URLSearchParams({ destino: destino.value, titulo: titulo.value, slug: slug.value });
      try {
        const r = await fetch('/api/sugestao?' + p, { credentials: 'same-origin' }).then((x) => x.json());
        if (r.categoria && cat.value === '') cat.options[0].textContent = `Detectar sozinho (${CATS[r.categoria][0]})`;
        if (!slugMexido) slug.placeholder = r.sugestao || 'aula-inss';
        if (!slug.value && r.sugestao) {
          box.classList.remove('bad'); msg.className = '';
          msg.innerHTML = `Sugestão: <b>${esc(r.sugestao)}</b> — deixe vazio para usar ou digite outro nome.`;
        } else if (slug.value) {
          box.classList.toggle('bad', !!r.slug_erro);
          msg.className = r.slug_erro ? 'err' : '';
          msg.innerHTML = r.slug_erro ? esc(r.slug_erro) : `<span style="color:var(--bs-success);font-weight:700"><i class="ti ti-check"></i> ${esc(host)}/${esc(slug.value)} está livre</span>`;
        } else {
          box.classList.remove('bad'); msg.className = ''; msg.innerHTML = msgPadrao;
        }
      } catch (e) {}
    }, 250);

    destino.addEventListener('input', () => { $('[data-err=destino]').textContent = ''; destino.classList.remove('bad'); sugerir(); });
    titulo.addEventListener('input', sugerir);
    slug.addEventListener('input', () => {
      const v = slug.value.toLowerCase().replace(/\s+/g, '-').normalize('NFD').replace(/[̀-ͯ]/g, '');
      if (v !== slug.value) slug.value = v;
      slugMexido = !!slug.value;
      sugerir();
    });
    // Tab no nome vazio aceita a sugestão
    slug.addEventListener('keydown', (e) => {
      if (e.key === 'Tab' && !slug.value && slug.placeholder && slug.placeholder !== 'aula-inss') {
        slug.value = slug.placeholder; slugMexido = true; sugerir();
      }
    });
    // o "required" do nome cai pra usar a sugestão quando a pessoa não digitou nada
    slug.required = false;
  };

  window.blCriar = async function (e) {
    e.preventDefault();
    const f = e.target, btn = $('#c-btn');
    const slug = f.slug.value || (f.slug.placeholder !== 'aula-inss' ? f.slug.placeholder : '');
    if (!slug) { toast('Escolha um nome para o link.', true); f.slug.focus(); return; }
    btn.disabled = true;
    try {
      const l = await api('POST', '/api/links', {
        destino: f.destino.value, slug, titulo: f.titulo.value, categoria: f.categoria.value, campanha: f.campanha.value,
      });
      await copiar(l.curto, true);
      $('#c-ok-url').textContent = l.curto;
      $('#c-ok-stats').href = `/app/links/${l.id}`;
      $('#c-ok-qr').onclick = () => blQR(l);
      $('#c-ok').classList.add('on');
      const linha = linhaNova({ id: l.id, slug: l.slug, destino: l.destino, titulo: l.titulo, categoria: l.categoria, campanha: l.campanha, ativo: true });
      $('#lista').prepend(linha);
      blFiltrar();
      f.destino.value = ''; f.slug.value = ''; f.titulo.value = ''; f.categoria.value = '';
      f.slug.placeholder = 'aula-inss';
      f.categoria.options[0].textContent = 'Detectar sozinho';
      $('#c-slug-msg').className = '';
      $('#c-slug-msg').innerHTML = 'Letras minúsculas, números e hífen. Ex.: <b>ebook-tjsp</b>';
      $('#c-slug-box').classList.remove('bad');
      toast('Link criado e copiado!');
    } catch (err) {
      if (/destino/i.test(err.message)) { $('[data-err=destino]').textContent = err.message; f.destino.classList.add('bad'); }
      toast(err.message, true);
    } finally { btn.disabled = false; }
  };

  // ── Gráfico de barras (uma série, com tooltip) ──────────────────────────
  window.blBarras = function (id, dados, tipo) {
    const el = document.getElementById(id);
    if (!el) return;
    const tip = $('.tip', el);
    const W = Math.max(280, el.clientWidth || 900), H = tipo === 'hora' ? 180 : 220, padL = 34, padB = 24, padT = 8;
    const n = dados.length;
    const max = Math.max(1, ...dados.map((d) => d.n));
    // eixo "redondo"
    const passo = Math.pow(10, Math.floor(Math.log10(max)));
    let topo = Math.ceil(max / passo) * passo;
    if ((topo / 2) % 1) topo += passo; // meio do eixo sempre inteiro (são cliques)
    const ticks = [0, topo / 2, topo];
    const cw = (W - padL) / n, bw = Math.max(2, Math.min(22, cw - 3));
    const rot = (d) => (tipo === 'dia' ? `${d.dia.slice(8, 10)}/${d.dia.slice(5, 7)}` : d.rot);
    const cadaN = Math.ceil(n / Math.max(3, Math.floor(W / (tipo === 'hora' ? 44 : 70))));
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Gráfico de cliques">`;
    s += '<g class="grid">' + ticks.map((t) => { const y = padT + (H - padB - padT) * (1 - t / topo); return `<line x1="${padL}" x2="${W}" y1="${y}" y2="${y}"/>`; }).join('') + '</g>';
    s += '<g class="axis">' + ticks.map((t) => { const y = padT + (H - padB - padT) * (1 - t / topo); return `<text x="${padL - 6}" y="${y + 4}" text-anchor="end">${num(t)}</text>`; }).join('');
    dados.forEach((d, i) => { if (i % cadaN === 0) s += `<text x="${padL + i * cw + cw / 2}" y="${H - 6}" text-anchor="middle">${rot(d)}</text>`; });
    s += '</g>';
    dados.forEach((d, i) => {
      const h = d.n ? Math.max(2, (H - padB - padT) * (d.n / topo)) : 0;
      const x = padL + i * cw + (cw - bw) / 2, y = H - padB - h;
      const r = Math.min(4, bw / 2, h);
      s += `<g class="col" data-i="${i}"><rect class="hit" x="${padL + i * cw}" y="${padT}" width="${cw}" height="${H - padB - padT}"/>`;
      if (h) s += `<path class="bar" d="M${x},${H - padB} V${y + r} Q${x},${y} ${x + r},${y} H${x + bw - r} Q${x + bw},${y} ${x + bw},${y + r} V${H - padB} Z" fill="var(--bs-bar)"/>`;
      s += '</g>';
    });
    s += '</svg>';
    el.insertAdjacentHTML('beforeend', s);
    const svg = $('svg', el);
    svg.addEventListener('mousemove', (e) => {
      const g = e.target.closest('g.col');
      if (!g) { tip.style.display = 'none'; return; }
      const d = dados[+g.dataset.i];
      const box = el.getBoundingClientRect(), sb = svg.getBoundingClientRect();
      const i = +g.dataset.i, esc_ = sb.width / W;
      const lbl = tipo === 'dia' ? new Date(d.dia + 'T12:00:00').toLocaleDateString('pt-BR', { weekday: 'short', day: '2-digit', month: '2-digit' }) : `${d.rot} – ${String((+d.rot.slice(0, 2) + 1) % 24).padStart(2, '0')}h`;
      tip.innerHTML = `${lbl}: <b>${num(d.n)}</b> clique${d.n === 1 ? '' : 's'}`;
      tip.style.left = (sb.left - box.left + (padL + i * cw + cw / 2) * esc_) + 'px';
      tip.style.top = (sb.top - box.top + (H - padB - (d.n ? (H - padB - padT) * (d.n / topo) : 0)) * esc_) + 'px';
      tip.style.display = 'block';
      $$('path.bar', svg).forEach((p) => (p.style.opacity = ''));
    });
    svg.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
  };

  // ── Usuários ────────────────────────────────────────────────────────────
  function formUsuario(u) {
    const f = $('#m-user form');
    f.reset();
    f.id.value = u ? u.id : '';
    f.nome.value = u ? u.nome : '';
    f.email.value = u ? u.email : '';
    f.email.disabled = !!u;
    f.papel.value = u ? u.papel : 'editor';
    f.ativo.value = u && !u.ativo ? '0' : '1';
    f.senha.required = !u;
    $('#mu-titulo').textContent = u ? 'Editar usuário' : 'Novo usuário';
    $('#mu-senha-lbl').textContent = u ? 'Nova senha (deixe vazio para manter)' : 'Senha';
    abrir('m-user');
  }
  window.blNovoUsuario = () => formUsuario(null);
  window.blEditarUsuario = (el) => formUsuario(JSON.parse(el.closest('[data-user]').dataset.user));
  window.blSalvarUsuario = async function (e) {
    e.preventDefault();
    const f = e.target;
    const corpo = { nome: f.nome.value, papel: f.papel.value, ativo: f.ativo.value === '1' };
    if (f.senha.value) corpo.senha = f.senha.value;
    try {
      if (f.id.value) await api('PATCH', `/api/usuarios/${f.id.value}`, corpo);
      else await api('POST', '/api/usuarios', { ...corpo, email: f.email.value });
      toast('Usuário salvo.');
      setTimeout(() => location.reload(), 400);
    } catch (err) { toast(err.message, true); }
  };
})();
