"""Leitura do clique: de onde veio, aparelho, navegador e se é robô/pré-visualização."""
import hashlib
import re
from urllib.parse import urlsplit

from . import config

# Apps que abrem o link sozinhos pra montar a pré-visualização (o card com imagem).
# Esses "cliques" não são pessoas e inflavam a contagem do Bitly.
_BOT_RE = re.compile(
    r"bot\b|bot/|crawler|spider|slurp|preview|facebookexternalhit|facebookcatalog|"
    r"whatsapp/|telegrambot|twitterbot|slackbot|discordbot|linkedinbot|skypeuripreview|"
    r"embedly|pinterest|vkshare|redditbot|applebot|bingpreview|google-inspectiontool|"
    r"headlesschrome|python-requests|python-urllib|curl/|wget/|go-http-client|okhttp|"
    r"axios/|node-fetch|httpclient|scrapy|ahrefs|semrush|mj12bot|dotbot|petalbot|"
    r"yandex|baiduspider|duckduckbot|ia_archiver|monitor|uptime|lighthouse",
    re.I,
)

# Origem pelo navegador interno dos apps (quando o app não manda Referer)
_INAPP = [
    (re.compile(r"Instagram", re.I), "Instagram"),
    (re.compile(r"FBAN|FBAV|FB_IAB|FBIOS", re.I), "Facebook"),
    (re.compile(r"musical_ly|TikTok|BytedanceWebview|trill", re.I), "TikTok"),
    (re.compile(r"Telegram", re.I), "Telegram"),
    (re.compile(r"LinkedInApp", re.I), "LinkedIn"),
    (re.compile(r"Twitter", re.I), "X / Twitter"),
]

# Origem pelo site que mandou a pessoa (Referer)
_HOSTS = [
    (("youtube.com", "youtu.be"), "YouTube"),
    (("instagram.com",), "Instagram"),
    (("facebook.com", "fb.com", "fb.me", "messenger.com"), "Facebook"),
    (("whatsapp.com", "wa.me"), "WhatsApp"),
    (("tiktok.com",), "TikTok"),
    (("t.me", "telegram.org", "telegram.me"), "Telegram"),
    (("google.", "googleusercontent.com"), "Google"),
    (("linkedin.com", "lnkd.in"), "LinkedIn"),
    (("twitter.com", "x.com", "t.co"), "X / Twitter"),
    (("mail.", "outlook.", "gmail."), "E-mail"),
    (("hotmart.com",), "Hotmart"),
    (("braboconcursos.com.br", "aprovasim.com"), "Site Brabo"),
]

# utm_source mais comuns → mesmo rótulo da origem
_UTM_SOURCES = {
    "youtube": "YouTube", "yt": "YouTube", "instagram": "Instagram", "ig": "Instagram",
    "facebook": "Facebook", "fb": "Facebook", "meta": "Facebook", "whatsapp": "WhatsApp",
    "wpp": "WhatsApp", "zap": "WhatsApp", "tiktok": "TikTok", "telegram": "Telegram",
    "google": "Google", "email": "E-mail", "e-mail": "E-mail", "activecampaign": "E-mail",
}


def is_bot(ua: str) -> bool:
    return not ua or bool(_BOT_RE.search(ua))


def origem(ua: str, referer: str, utm_source: str) -> tuple[str, str]:
    """(origem legível, host do referer)."""
    host = ""
    if referer:
        try:
            host = (urlsplit(referer).hostname or "").lower().removeprefix("www.").removeprefix("m.")
        except ValueError:
            host = ""
    src = _UTM_SOURCES.get((utm_source or "").strip().lower())
    if src:
        return src, host
    for rx, nome in _INAPP:
        if rx.search(ua or ""):
            return nome, host
    if host:
        for chaves, nome in _HOSTS:
            if any(host == c or host.endswith("." + c) or (c.endswith(".") and c in host) for c in chaves):
                return nome, host
        base = config.PUBLIC_BASE.split("://", 1)[-1].split("/")[0].lower()
        if host == base:
            return "Direto", host
        return "Outros sites", host
    if utm_source:
        return utm_source.strip()[:30], host
    return "Direto", host


def dispositivo(ua: str) -> tuple[str, str, str]:
    """(tipo, sistema, navegador)."""
    ua = ua or ""
    if re.search(r"iPad|Tablet|PlayBook|Silk|(Android(?!.*Mobile))", ua):
        tipo = "Tablet"
    elif re.search(r"Mobi|iPhone|iPod|Android.*Mobile|Windows Phone", ua):
        tipo = "Celular"
    elif ua:
        tipo = "Computador"
    else:
        tipo = "Desconhecido"

    if re.search(r"iPhone|iPad|iPod", ua):
        so = "iOS"
    elif "Android" in ua:
        so = "Android"
    elif "Windows" in ua:
        so = "Windows"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        so = "macOS"
    elif "CrOS" in ua:
        so = "ChromeOS"
    elif "Linux" in ua:
        so = "Linux"
    else:
        so = "Outro"

    for rx, nome in [
        (r"Instagram", "Instagram (app)"), (r"FBAN|FBAV|FB_IAB", "Facebook (app)"),
        (r"musical_ly|TikTok|BytedanceWebview", "TikTok (app)"),
        (r"SamsungBrowser", "Samsung Internet"), (r"Edg/|EdgA/|EdgiOS", "Edge"),
        (r"OPR/|Opera", "Opera"), (r"Firefox|FxiOS", "Firefox"),
        (r"CriOS|Chrome", "Chrome"), (r"Safari", "Safari"),
    ]:
        if re.search(rx, ua):
            return tipo, so, nome
    return tipo, so, "Outro"


def visitante(ip: str, ua: str) -> str:
    """Identificador anônimo (o IP não é guardado): mesma pessoa/aparelho → mesmo hash."""
    return hashlib.sha256(f"{config.SESSION_SECRET}|{ip}|{ua}".encode()).hexdigest()[:16]


def client_ip(headers, fallback: str) -> str:
    for h in ("cf-connecting-ip", "x-real-ip"):
        if headers.get(h):
            return headers[h].strip()
    xff = headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return fallback or ""
