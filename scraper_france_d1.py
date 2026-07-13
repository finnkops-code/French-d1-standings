#!/usr/bin/env python3
"""
France Division 1 Baseball – Standen scraper
=============================================
Haalt de standen op van:
    https://ffbs.wbsc.org/en/events/2026-championnat-de-france-division-1-baseball/standings
en schrijft ze weg als standen_france_d1.json.

Deze site draait op hetzelfde WBSC-standen-sjabloon als stats.baseball.cz
(zelfde structuur: <h3>-fase-headers gevolgd door een <table>), dus de
parse-aanpak is identiek aan de Tsjechische standen-scraper. Twee verschillen:
  1. Het teamlogo staat hier al als losse <img>-tag in de tabel zelf, dus die
     wordt direct meegescraped (in plaats van een hardcoded PHP-logolijst,
     die voor Franse teams niet bestaat).
  2. De fase-header wordt gezocht met <h2>/<h3>/<h4> (in plaats van alleen
     <h3>) voor wat meer robuustheid, mocht het sjabloon licht afwijken.

Let op: dit script gebruikt bewust dezelfde simpele urllib-aanpak als de
Tsjechische standen-scraper (geen Playwright/proxy-fallback). Mocht deze site
— net als stats.baseball.cz — GitHub Actions-runners op WAF/CDN-niveau gaan
blokkeren (403 al bij de eerste request), dan is dezelfde hardening (Playwright
+ stealth + optionele proxy) die daar is toegepast ook hier over te nemen.
"""
import json
import re
import urllib.request
from datetime import datetime, timezone
URL = "https://ffbs.wbsc.org/en/events/2026-championnat-de-france-division-1-baseball/standings"
def fetch_html(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")
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
            tds = [re.sub(r'\s+', ' ', td).strip() for td in tds]
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
            # Logo staat meestal in de cel vlak vóór de teamnaam-cel (het losse
            # vlag-icoontje); val terug op de hele rij als dat niet zo blijkt.
            logo = ''
            if team_idx - 1 >= 0 and team_idx - 1 < len(tds_raw):
                logo = extract_logo(tds_raw[team_idx - 1])
            if not logo:
                logo = extract_logo(row)
            # Cijfers volgen na de teamnaam-cel
            cijfers = [c for c in tds[team_idx + 1:] if c != '']
            if len(cijfers) < 3:
                continue
            rij = {
                "positie": positie,
                "team":    team,
                "logo":    logo,
                "w":       cijfers[0] if len(cijfers) > 0 else '-',
                "l":       cijfers[1] if len(cijfers) > 1 else '-',
                "t":       cijfers[2] if len(cijfers) > 2 else '-',
                "pct":     cijfers[3] if len(cijfers) > 3 else '-',
                "gb":      cijfers[4] if len(cijfers) > 4 else '-',
            }
            fase_rijen.append(rij)
        if fase_rijen:
            result[fase_naam] = fase_rijen
        i += 2
    return result
def main():
    print(f"Ophalen van {URL}...")
    html = fetch_html(URL)
    print(f"Ontvangen: {len(html)} bytes")
    standen = parse_standings(html)
    print(f"Gevonden fases: {list(standen.keys())}")
    output = {
        "bijgewerkt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron":       URL,
        "standen":    standen,
    }
    with open("standen_france_d1.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("✅ standen_france_d1.json opgeslagen")
if __name__ == "__main__":
    main()
