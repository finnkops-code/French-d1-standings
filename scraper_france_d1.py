#!/usr/bin/env python3
"""
France Division 1 Baseball – Standen scraper
=============================================
Primaire bron:  https://baseballtv.fr/en/classements/
                (WordPress, server-gerenderd, geen bot-detectie — direct
                 bereikbaar vanaf GitHub Actions)
Fallback-bron:  https://ffbs.wbsc.org/.../standings
                (WBSC-WAF blokkeert GitHub Actions-IP's; alleen bereikbaar
                 via de eigen Cloudflare Worker fetch-proxy, zie worker.js)

Schrijft de standen weg als standen_france_d1.json in hetzelfde formaat
als de Tsjechische standen-scraper (fases → rijen met positie/team/logo/
w/l/t/pct/gb), zodat de PHP-widget ongewijzigd blijft werken.

Verschillen tussen de twee bronnen die de parser afhandelt:
  - baseballtv.fr heeft geen T-kolom (W, L, PCT, GB); WBSC wel
    (W, L, T, PCT, GB). De kolomindeling wordt gedetecteerd op de
    positie van het PCT-getal (bevat een punt).
  - baseballtv.fr toont geen logo's in de tabelrijen; die worden
    aangevuld uit de hardcoded logolijst hieronder (WBSC-logo-URLs).
"""
import html as html_mod
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

PRIMARY_URL  = "https://baseballtv.fr/en/classements/"
FALLBACK_URL = "https://ffbs.wbsc.org/en/events/2026-championnat-de-france-division-1-baseball/standings"

# Cloudflare Worker fetch-proxy (zie worker.js). Zolang WORKER_URL leeg is
# wordt die tier overgeslagen. Instellen via env-variabelen in de workflow.
WORKER_URL = os.environ.get("WORKER_URL", "").rstrip("/")
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "")

# Logo's per teamcode (static.wbsc.org) — baseballtv.fr toont ze niet in de
# standentabel zelf, dus we vullen ze hieruit aan.
TEAM_LOGOS = {
    "BEZ": "https://static.wbsc.org/upload/54f7aee7-2dea-f878-4244-591fd30486d0.jpg",
    "LAR": "https://static.wbsc.org/upload/0050079f-5f9e-d803-8839-20992d9cfc18.jpg",
    "MTP": "https://static.wbsc.org/upload/51a28c3c-a894-cc4d-30ca-b8c705e5bd5d.png",
    "PUC": "https://static.wbsc.org/upload/01986991-2958-d994-bd39-28ab3d9d6466.jpg",
    "ROU": "https://static.wbsc.org/assets/cms/teams/logo/a18c7fc9-a3ce-81e9-0c12-f2a5ec8eab1e.jpg",
    "SAV": "https://static.wbsc.org/upload/e20fa02b-5959-2c18-2def-c682eb6a540f.jpg",
    "SEN": "https://static.wbsc.org/assets/cms/teams/logo/1071d9a2-1565-7750-d6bd-c5b9217a804f.jpg",
    "TOU": "https://static.wbsc.org/upload/83428e38-f463-52e1-0571-48f71aa9fc31.jpg",
}


def _http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_html(url, moet_bevatten="<table"):
    """
    Tiers:
      1. direct           — voor het geval de blokkade ooit verdwijnt
      2. cloudflare worker — eigen fetch-proxy (zie worker.js), IP-range
                             die niet op de WBSC-blokkadelijst staat
      3. allorigins       — publieke proxy, geeft ruwe HTML terug
      4. corsproxy.io     — tweede publieke proxy als backup
    Een tier telt pas als geslaagd wanneer 'moet_bevatten' in de
    response staat (zodat een challenge-/foutpagina zonder
    standentabel niet als succes telt).
    """
    tiers = [("direct", url)]
    if WORKER_URL:
        worker = f"{WORKER_URL}/?url={urllib.parse.quote(url, safe='')}"
        if WORKER_TOKEN:
            worker += f"&token={urllib.parse.quote(WORKER_TOKEN, safe='')}"
        tiers.append(("cloudflare worker", worker))
    tiers += [
        ("allorigins", f"https://api.allorigins.win/raw?url={urllib.parse.quote(url, safe='')}"),
        ("corsproxy.io", f"https://corsproxy.io/?url={urllib.parse.quote(url, safe='')}"),
    ]
    laatste_fout = None
    for naam, fetch_url in tiers:
        print(f"   Tier: {naam}...")
        try:
            html = _http_get(fetch_url)
            if moet_bevatten.lower() in html.lower():
                print(f"   ✓ Gelukt via {naam} ({len(html)} bytes)")
                return html
            print(f"   ✗ {naam}: response zonder verwachte inhoud ({len(html)} bytes)")
        except Exception as e:
            laatste_fout = e
            print(f"   ✗ {naam} mislukt: {e}")
    raise RuntimeError(f"Alle fetch-tiers mislukt (laatste fout: {laatste_fout})")


def clean_team_name(text):
    """Strip the team-code prefix (e.g. 'LAR La Rochelle Boucaniers' → 'La Rochelle Boucaniers')."""
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'^[A-Z]{2,4}\s+', '', text)
    return text.strip()


def extract_logo(cell_html):
    """Haalt de src van de eerste <img> in een tabelcel op, of '' als er geen is."""
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', cell_html)
    return m.group(1) if m else ''


def parse_standings(html):
    result = {}
    # Split op fase-headers (h2/h3/h4 — de meeste WBSC-standenpagina's gebruiken h3,
    # maar we zijn iets ruimer voor het geval het sjabloon afwijkt).
    parts = re.split(r'<h[234][^>]*>(.*?)</h[234]>', html, flags=re.DOTALL)
    i = 1
    while i < len(parts):
        fase_naam = re.sub(r'<[^>]+>', '', parts[i]).strip()
        rest = parts[i + 1] if i + 1 < len(parts) else ''
        table_match = re.search(r'<table[^>]*>(.*?)</table>', rest, re.DOTALL)
        if not table_match:
            i += 2
            continue
        table_html = table_match.group(1)
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL)
        fase_rijen = []
        for row in rows:
            tds_raw = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            tds = [re.sub(r'<[^>]+>', '', td).strip() for td in tds_raw]
            tds = [html_mod.unescape(re.sub(r'\s+', ' ', td)).strip() for td in tds]
            if len(tds) < 5:
                continue
            positie = tds[0] if tds[0] else '-'
            # Team-cel vinden: de eerste cel (na positie) met letters erin.
            team = ''
            team_idx = -1
            for j in range(1, len(tds)):
                if re.search(r'[A-Za-z]', tds[j]):
                    team = clean_team_name(tds[j])
                    team_idx = j
                    break
            if not team or team_idx == -1:
                continue
            # Teamcode (voor de logo-aanvulling) uit de ongestripte celtekst
            code_m = re.match(r'^([A-Z]{2,4})\s', re.sub(r'\s+', ' ', tds[team_idx]).strip())
            team_code = code_m.group(1) if code_m else ''
            # Logo: eerst uit de rij zelf (WBSC-bron), anders uit de
            # hardcoded lijst op teamcode (baseballtv.fr-bron).
            logo = ''
            if team_idx - 1 >= 0 and team_idx - 1 < len(tds_raw):
                logo = extract_logo(tds_raw[team_idx - 1])
            if not logo:
                logo = extract_logo(row)
            if not logo:
                logo = TEAM_LOGOS.get(team_code, '')
            # Cijfers volgen na de teamnaam-cel. Kolomindeling detecteren op
            # de positie van PCT (het enige getal met een punt erin):
            #   WBSC:         W L T PCT GB   (pct op index 3)
            #   baseballtv:   W L   PCT GB   (pct op index 2, geen T-kolom)
            cijfers = [re.sub(r'[—–]', '-', c) for c in tds[team_idx + 1:] if c != '']
            if len(cijfers) < 3:
                continue
            pct_idx = next((k for k, c in enumerate(cijfers) if '.' in c), None)
            if pct_idx == 2:      # geen T-kolom
                t_val = '0'
                gb_idx = 3
            elif pct_idx == 3:    # met T-kolom
                t_val = cijfers[2]
                gb_idx = 4
            else:                 # onbekende indeling: oude gedrag
                t_val = cijfers[2] if len(cijfers) > 2 else '-'
                pct_idx = 3
                gb_idx = 4
            rij = {
                "positie": positie,
                "team":    team,
                "logo":    logo,
                "w":       cijfers[0],
                "l":       cijfers[1],
                "t":       t_val,
                "pct":     cijfers[pct_idx] if pct_idx is not None and pct_idx < len(cijfers) else '-',
                "gb":      cijfers[gb_idx] if gb_idx < len(cijfers) else '-',
            }
            fase_rijen.append(rij)
        if fase_rijen:
            result[fase_naam] = fase_rijen
        i += 2
    return result


def main():
    html = None
    bron = None
    standen = {}

    for url in (PRIMARY_URL, FALLBACK_URL):
        print(f"Ophalen van {url}...")
        try:
            html = fetch_html(url)
        except Exception as e:
            print(f"⚠️  {url} mislukt: {e}")
            continue
        # Alles vanaf de individuele-statistieken-sectie afknippen, zodat
        # leader-tabellen nooit als 'fase' geparsed worden (baseballtv.fr).
        knip = re.search(r'Season leaders|Individual statistics', html)
        parse_html = html[:knip.start()] if knip else html
        standen = parse_standings(parse_html)
        if standen:
            bron = url
            break
        print(f"⚠️  {url}: geen standen geparsed, volgende bron proberen")

    print(f"Gevonden fases: {list(standen.keys())}")

    if not standen:
        print("⚠️  Geen standen geparsed uit welke bron dan ook")
        if html:
            print("Eerste 2000 tekens van de laatste pagina:")
            print(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))[:2000])
        sys.exit(1)

    output = {
        "bijgewerkt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron":       bron,
        "standen":    standen,
    }
    with open("standen_france_d1.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("✅ standen_france_d1.json opgeslagen")


if __name__ == "__main__":
    main()
