#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stase Toscana - scraper per Portale Eventi (https://portaleventi.it), sola Toscana.

Copia SOLO: titolo, date, luogo, organizzatore.
NON copia: descrizioni, immagini, programmi, testi editoriali.

Ogni evento prodotto porta "fonte": "portaleventi" e "url" della scheda originale.
La policy bot di portaleventi.it (robots.txt) dice esplicitamente:
  "chi cita la fonte con link nel risultato lato utente ALLOW;
   chi prende contenuti per training/mirror senza ritorno BLOCK"
Quindi la citazione con link NON e' un dettaglio: e' la condizione alle quale il
sito permette la lettura. Chi rimuove il badge e il link alla fonte esce da quella
condizione e si assume il rischio (vedi anche dir. 96/9/CE sul diritto sui generis
delle banche dati: l'estrazione di una parte sostanziale va autorizzata).

A differenza di Terre di Pisa qui le date sono esatte: ogni scheda espone un
JSON-LD schema.org/Event con startDate/endDate. Nessun modello, nessuna data
"indicativa", nessun rischio di inventare giorni.

Uso:
  python scripts/scrape_portaleventi.py --dry-run --max 15   # prova, non scrive
  python scripts/scrape_portaleventi.py                      # normale
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

BASE = "https://portaleventi.it"
REGIONE = "Toscana"
LISTA = BASE + "/eventi?regione=" + quote(REGIONE)
UA = "StaseToscanaBot/1.0 (+https://stasetoscana.it)"
PAUSA = 1.5          # secondi tra due richieste (vale anche per Nominatim: max 1/s)
MAX_GIORNI = 40      # scarta eventi piu' lunghi (stagioni, rassegne, mostre permanenti)
GIORNI_RILETTURA_FUTURI = 7  # rilettura delle schede di eventi futuri (le date cambiano di rado)
GIORNI_RIPROVA_KO = 7  # una scheda non capita si ritenta dopo N giorni (non ogni giorno)
MAX_PAGINE = 90      # tetto di sicurezza sulla paginazione della lista regione

SOGLIA_KO = 0.3       # se oltre il 30% delle schede lette non ha JSON-LD, il run fallisce
MIN_CAMPIONE_KO = 10   # ...ma solo se ne sono state lette almeno tante
STATI_DA_SCARTARE = ("cancelled", "postponed")   # schema.org eventStatus

VERSIONE_CACHE = 4   # sale quando cambia cio' che si legge da ogni scheda
F_OUT = "eventi_portaleventi.json"
F_GEO = "geocode_cache_portaleventi.json"
F_PAG = "cache_portaleventi.json"

MESI = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]

# Le 10 province toscane: sigla -> nome esteso (come nei dati Stase/VT)
PROVINCE = {
    "AR": "Arezzo", "FI": "Firenze", "GR": "Grosseto", "LI": "Livorno", "LU": "Lucca",
    "MS": "Massa-Carrara", "PI": "Pisa", "PT": "Pistoia", "PO": "Prato", "SI": "Siena",
}

# Categorie del sito, in ordine di priorita': vince la prima regola che combacia
# col titolo. Identiche a scrape_sagre.py, cosi' la tassonomia resta una sola.
_CIBO = r"castagn|tartuf|polenta|\bvino\b|\bolio\b|fung|cinghial|pesce|fritt|tortell|bistecc|salsicc|formagg|miele|zucca|marron"
REGOLE_CATEGORIA = [
    ("Rievocazione storica/Palio", r"\bpalio|rievocazion|medioev|medieval|giostra|calcio storico|disfida|corteo storico|gioco del ponte"),
    ("Evento sportivo", r"maratona|\bcorsa\b|\btrail\b|trekking|camminata|cicloturis|ciclostoric|\bbike\b|torneo|\bgara\b|campionato|regata|triathlon|pedalata|podistic"),
    ("Sagra", r"\bsagr[ae]\b|\b(festa|fiera|mostra[- ]mercato)\b.*(" + _CIBO + ")"),
    ("Mercato", r"\bmercat|antiquari|\bantiqua\b|hobbismo|collezionismo|brocante"),
    ("Fiera", r"\bfiere?\b|\bsalone\b|\bexpo\b"),
    ("Evento gastronomico", r"cantin|degusta|\bvini\b|\bvino\b|enogastron|\bgusto\b|\bcena\b|\bpranzo\b|aperitivo|street food|assaggi|gastronom|" + _CIBO),
    ("Concerto", r"concert|sinfonic|orchestra|\bcoro\b|\bmusica\b|\bmusic\b|\bjazz\b|\bblues\b|\brock\b|recital|cantautor|\bband\b"),
    ("Festival", r"festival"),
    ("Spettacolo teatrale", r"teatr|\bprosa\b|commedia|spettacol|\bdanza\b|balletto|opera lirica|musical|cinema|cortometraggi|\bfilm\b|cabaret"),
    ("Mostra/Esposizione", r"\bmostr|esposizion|\bmuse[oi]\b|galleria|pinacoteca"),
    ("Serata/Discoteca", r"discotec|\bdj\b|\bparty\b|\bserata\b"),
    ("Festa paesana", r"\bfest[ae]\b|natale|presepi|befana|carnevale|halloween|capodanno"),
]
_REGOLE_COMPILATE = [(c, re.compile(r)) for c, r in REGOLE_CATEGORIA]


# ----------------------------------------------------------------------------- rete

S = requests.Session()
S.headers["User-Agent"] = UA
_ultimo = 0.0
ROBOTS = RobotFileParser()


def get(url, **kw):
    """GET educata: una richiesta ogni PAUSA secondi, user-agent riconoscibile."""
    global _ultimo
    attesa = PAUSA - (time.time() - _ultimo)
    if attesa > 0:
        time.sleep(attesa)
    try:
        r = S.get(url, timeout=30, **kw)
    finally:
        _ultimo = time.time()
    r.raise_for_status()
    return r


def carica_robots():
    """Legge robots.txt e lo rende disponibile ai controlli can_fetch()."""
    try:
        testo = get(BASE + "/robots.txt").text
    except Exception as e:
        print(f"! robots.txt non leggibile ({e}): procedo con prudenza")
        ROBOTS.parse([])
        return
    ROBOTS.parse(testo.splitlines())


# ------------------------------------------------------------------------ elenco

RE_LINK_EVENTO = re.compile(r'href="(/evento/[^"?#]+)"')


def url_eventi(limite=0):
    """[(url, None)] delle schede evento della sola Toscana, dalla lista paginata.
    Con limite > 0 si ferma appena ne ha raccolte abbastanza (utile per --max)."""
    visti, out = set(), []
    for pagina in range(1, MAX_PAGINE + 1):
        url = LISTA if pagina == 1 else f"{LISTA}&page={pagina}"
        if not ROBOTS.can_fetch(UA, url):
            print(f"- vietato da robots.txt: {url}")
            break
        try:
            html = get(url).text
        except Exception as e:
            print(f"! pagina elenco {pagina} non leggibile: {e}")
            break
        nuovi = 0
        for m in RE_LINK_EVENTO.finditer(html):
            u = urljoin(BASE, m.group(1))
            if u not in visti:
                visti.add(u)
                out.append((u, None))
                nuovi += 1
        print(f"  elenco pagina {pagina}: +{nuovi} (totale {len(out)})")
        if nuovi == 0:
            break
        if limite and len(out) >= limite:
            break
    return out


# ------------------------------------------------------------------------ parsing

def _analizza_html(html):
    """Testo visibile + il comune ufficiale dichiarato dalla scheda.

    Nel blocco del luogo c'e' sempre un link al comune:
        <a href="/comune/pietrasanta" class="comune-link">Pietrasanta (LU)</a>
    ed e' l'unico punto affidabile. Il JSON-LD spesso porta la FRAZIONE
    ("Marina di Pietrasanta", "Ponte a Egola"): cercare la sigla accanto alla
    frazione non trova mai niente e la provincia restava vuota.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    testo = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    comune, sigla = "", ""
    link = soup.select_one("a.comune-link")   # solo il blocco del luogo: i comuni
    # degli eventi correlati usano un'altra classe e non vanno presi in considerazione
    if link:
        etichetta = re.sub(r"\s+", " ", link.get_text(" ", strip=True))
        m = re.match(r"^(.*?)\s*\(([A-Z]{2})\)$", etichetta)
        if m:
            comune, sigla = m.group(1).strip(), m.group(2)
    return testo, comune, sigla


def normalizza_comune(nome):
    """'Montecatini-Terme' -> 'Montecatini Terme'.

    Il trattino non e' un errore del portale: la denominazione ISTAT di quel
    comune lo usa davvero ("Montecatini-Terme"). Lo togliamo lo stesso perche'
    la forma con lo spazio e' quella usata da Google Maps, da Wikipedia e dalla
    maggior parte delle fonti, e perche' lo stesso comune scritto in due modi
    dentro un solo JSON e' solo confusione.

    Nota: la deduplica del sito non ne soffriva — la chiave passa da _normTesto(),
    che converte la punteggiatura in spazi, quindi 'Montecatini-Terme' e
    'Montecatini Terme' finivano gia' nello stesso gruppo.
    """
    n = re.sub(r"(?<=\w)\s*[-\u2010-\u2015]\s*(?=\w)", " ", nome or "")
    return re.sub(r"\s+", " ", n).strip()


def provincia_da_testo(testo, comune):
    """Ripiego, usato solo se la scheda non ha il link al comune."""
    if not comune:
        return ""
    # tollera trattino e spazio indifferentemente ('Montecatini Terme' trova
    # anche 'Montecatini-Terme (PT)')
    pattern = r"[\s-]+".join(re.escape(p) for p in comune.split())
    m = re.search(rf"{pattern}\s*\(([A-Z]{{2}})\)", testo)
    return PROVINCE.get(m.group(1), "") if m else ""


def parse_scheda(html, url_pagina):
    """Legge il JSON-LD schema.org/Event. Restituisce None se non c'e' o non e' valido."""
    for blocco in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            dati = json.loads(blocco)
        except Exception:
            continue
        if not isinstance(dati, dict) or dati.get("@type") != "Event":
            continue
        nome = (dati.get("name") or "").strip()
        inizio_txt = (dati.get("startDate") or "")[:10]
        fine_txt = (dati.get("endDate") or inizio_txt)[:10]
        if not nome or not re.match(r"^\d{4}-\d{2}-\d{2}$", inizio_txt):
            continue
        try:
            inizio = date.fromisoformat(inizio_txt)
            fine = date.fromisoformat(fine_txt) if re.match(r"^\d{4}-\d{2}-\d{2}$", fine_txt) else inizio
        except ValueError:
            continue
        if fine < inizio:
            fine = inizio
        luogo = dati.get("location") or {}
        indirizzo = luogo.get("address") or {}
        testo, comune_link, sigla = _analizza_html(html)
        comune_json = (indirizzo.get("addressLocality") or "").strip()
        if not comune_json:
            nome_luogo = (luogo.get("name") or "").strip()
            comune_json = nome_luogo.split(",")[-1].strip() if nome_luogo else ""
        # il link al comune batte il JSON-LD: quello porta spesso la frazione
        comune = normalizza_comune(comune_link or comune_json)
        # la frazione, se c'e', resta nell'indirizzo e non sporca il campo comune
        frazione = ""
        for parte in (luogo.get("name") or "").split(","):
            parte = parte.strip()
            if parte and parte.lower() != comune.lower() and parte.lower() not in comune.lower():
                frazione = parte
        organizzatore = (dati.get("organizer") or {}).get("name") or ""
        stato = str(dati.get("eventStatus") or "").rsplit("/", 1)[-1].lower()   # "EventCancelled" -> "eventcancelled"
        annullato = any(x in stato for x in STATI_DA_SCARTARE)
        return {
            "nome": nome,
            "inizio": inizio,
            "fine": fine,
            "comune": comune,
            "frazione": frazione,
            "via": (indirizzo.get("streetAddress") or "").strip(),
            "provincia": PROVINCE.get(sigla, "") or provincia_da_testo(testo, comune),
            "organizzatore": organizzatore.strip(),
            "url": (dati.get("url") or url_pagina).strip(),
            "annullato": annullato,
        }
    return None


def categoria(titolo):
    """Assegna una delle categorie del sito in base alle parole del titolo."""
    t = "".join(c for c in unicodedata.normalize("NFD", titolo.lower()) if unicodedata.category(c) != "Mn")
    for nome, regola in _REGOLE_COMPILATE:
        if regola.search(t):
            return nome
    return "Altro"


def fmt_periodo(dal, al):
    if dal == al:
        return f"{dal.day} {MESI[dal.month - 1]} {dal.year}"
    if (dal.year, dal.month) == (al.year, al.month):
        return f"{dal.day}\u2013{al.day} {MESI[al.month - 1]} {al.year}"
    return f"{dal.day} {MESI[dal.month - 1]} \u2013 {al.day} {MESI[al.month - 1]} {al.year}"


# ---------------------------------------------------------------------- geocoding

def query_geo(via, comune):
    """Ricerche da tentare: via + comune, poi solo comune (approssimata)."""
    tentativi = []
    if comune:
        tentativi.append((f"{via}, {comune}" if via else comune, False))
        if via:
            tentativi.append((comune, True))
    return tentativi


def indirizzo_pulito(via, frazione, comune):
    """'Viale Morin 16, Marina di Pietrasanta, Pietrasanta', senza ripetizioni."""
    fuori = []
    for p in (via, frazione, comune):
        if p and p.lower() not in [o.lower() for o in fuori]:
            fuori.append(p)
    return ", ".join(fuori)


def nominatim(q, comune_livello=False):
    params = {
        "format": "jsonv2", "limit": 1, "countrycodes": "it", "bounded": 1,
        "viewbox": "9.6,44.55,12.45,42.2",   # riquadro della Toscana
        "q": q + ", Toscana, Italia",
    }
    if comune_livello:
        params["featureType"] = "settlement"
    j = get("https://nominatim.openstreetmap.org/search", params=params).json()
    if not j:
        return None
    if comune_livello and j[0].get("addresstype") in ("county", "state", "region", "province"):
        return None
    return [float(j[0]["lat"]), float(j[0]["lon"])]


def geocodifica(via, comune, cache, solo_cache=False):
    """Con solo_cache=True non chiama Nominatim: usa solo cio' che e' gia' in memoria."""
    if not comune:
        return None, None, True
    for q, approx in query_geo(via, comune):
        chiave = ("@comune:" + q) if approx else q
        if chiave not in cache:
            if solo_cache:
                continue
            try:
                cache[chiave] = nominatim(q, comune_livello=approx)
            except Exception as e:
                print(f"  ! geocoding fallito per '{q}': {e}")
                continue
        if cache[chiave]:
            return cache[chiave][0], cache[chiave][1], approx
    return None, None, True


# ---------------------------------------------------------------------------- main

def carica_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def salva_json(path, dati):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, indent=1, sort_keys=True)


def fresca(c, oggi):
    """True se la scheda in cache e' ancora buona.
    Future: riletta ogni GIORNI_RILETTURA_FUTURI giorni. Passate: ogni 30. Non capite (ko): si ritentano
    ogni GIORNI_RIPROVA_KO giorni, non a ogni giro — prima venivano riscaricate
    ogni giorno e si mangiavano il tetto di schede per esecuzione."""
    if not c or c.get("v") != VERSIONE_CACHE or not c.get("letta"):
        return False
    try:
        eta = (oggi - date.fromisoformat(c["letta"])).days
    except Exception:
        return False
    if c.get("ko"):
        return eta < GIORNI_RIPROVA_KO
    try:
        passato = date.fromisoformat(c["fine"]) < oggi
    except Exception:
        return False
    return eta < (30 if passato else GIORNI_RILETTURA_FUTURI)


def elabora_pagina(url):
    """Legge la scheda e restituisce il record da mettere in cache (o None)."""
    html = get(url).text
    p = parse_scheda(html, url)
    if not p:
        return None
    return {
        "v": VERSIONE_CACHE, "letta": date.today().isoformat(),
        "lungo": (p["fine"] - p["inizio"]).days > MAX_GIORNI,
        "titolo": p["nome"], "inizio": p["inizio"].isoformat(), "fine": p["fine"].isoformat(),
        "comune": p["comune"], "frazione": p["frazione"],
        "via": p["via"], "provincia": p["provincia"],
        "organizzatore": p["organizzatore"], "url": p["url"],
        "annullato": p["annullato"],
    }


def main():
    sys.stdout.reconfigure(line_buffering=True)      # log immediato anche in Actions
    t0 = time.time()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="non scrive nessun file")
    ap.add_argument("--max", type=int, default=0, help="esamina al massimo N schede (per le prove)")
    ap.add_argument("--max-fetch", type=int, default=int(os.environ.get("MAX_FETCH", 120)),
                    help="schede nuove o scadute da scaricare per esecuzione")
    ap.add_argument("--budget-min", type=float, default=float(os.environ.get("BUDGET_MIN", 15)),
                    help="tempo massimo in minuti: poi salva quello che ha")
    args = ap.parse_args()
    scaduto = lambda: (time.time() - t0) > args.budget_min * 60
    oggi = date.today()

    carica_robots()
    print(f"Elenco eventi {REGIONE} da {LISTA}")
    urls = url_eventi(args.max)
    if not urls:
        print("Nessuna scheda trovata: non aggiorno nulla.")
        return 1
    if args.max:
        urls = urls[:args.max]

    cache_pag = {} if args.dry_run else carica_json(F_PAG, {})
    print(f"Schede in elenco: {len(urls)} | gia' in memoria: {sum(1 for u, _ in urls if u in cache_pag)} "
          f"| tetto per esecuzione: {args.max_fetch} schede, {args.budget_min:g} minuti")

    # ---- fase 1: leggere le schede (con tetto e limite di tempo)
    # le schede mai viste hanno la precedenza su quelle solo da rinfrescare
    urls.sort(key=lambda x: x[0] in cache_pag)      # ordinamento stabile
    nuova_cache, errori, scaricate, rimandate, non_capite = {}, 0, 0, 0, 0
    for url, _ in urls:
        c = cache_pag.get(url)
        if fresca(c, oggi):
            nuova_cache[url] = c
            continue
        if scaricate >= args.max_fetch or scaduto():
            rimandate += 1
            if c:
                nuova_cache[url] = c
            continue
        if not ROBOTS.can_fetch(UA, url):
            print(f"- vietato da robots.txt: {url}")
            continue
        scaricate += 1
        try:
            rec = elabora_pagina(url)
        except Exception as e:
            errori += 1
            print(f"! errore su {url}: {e}")
            if c:
                nuova_cache[url] = c
            continue
        if rec:
            nuova_cache[url] = rec
            print(f"  scheda {scaricate} ({int(time.time() - t0)}s): {rec['titolo'][:60]} "
                  f"[{rec['inizio']} > {rec['fine']}]")
        else:
            non_capite += 1
            nuova_cache[url] = {"v": VERSIONE_CACHE, "ko": True, "letta": oggi.isoformat()}
    print(f"Schede lette ora: {scaricate} | senza JSON-LD: {non_capite} "
          f"| rimandate: {rimandate} | errori: {errori} | {int(time.time() - t0)}s")

    # se il portale ha cambiato l'HTML, quasi tutte le schede diventano "non capite":
    # meglio un run rosso (e la mail di GitHub) che un sito che si svuota piano piano
    if scaricate >= MIN_CAMPIONE_KO and non_capite / scaricate > SOGLIA_KO:
        print(f"! {non_capite} schede su {scaricate} senza JSON-LD valido (>{SOGLIA_KO:.0%}): "
              "il portale potrebbe aver cambiato struttura. Non aggiorno nulla.")
        return 3

    # ---- fase 2: costruire gli eventi dalla memoria
    cache_geo = carica_json(F_GEO, {})
    eventi, visti, senza_coord = [], set(), 0
    for url, rec in nuova_cache.items():
        if rec.get("ko") or rec.get("lungo"):
            continue          # scheda non capita, oppure evento troppo lungo da pubblicare
        if rec.get("annullato"):
            continue          # annullato o rinviato senza nuova data: non si pubblica
        inizio = date.fromisoformat(rec["inizio"])
        fine = date.fromisoformat(rec["fine"])
        if fine < oggi:
            continue                                  # finito
        comune, via, frazione = rec.get("comune", ""), rec.get("via", ""), rec.get("frazione", "")
        # a tempo scaduto non si chiama piu' Nominatim, ma le coordinate gia' in
        # memoria si usano: altrimenti l'evento sparirebbe dal file pubblicato
        lat, lon, approx = geocodifica(via, comune, cache_geo, solo_cache=scaduto())
        if lat is None:
            senza_coord += 1
            continue
        nome = rec["titolo"]
        indirizzo = indirizzo_pulito(via, frazione, comune)
        slug = urlparse(url).path.strip("/").split("/")[-1]
        chiave = (nome.lower(), inizio.isoformat(), comune.lower())
        if chiave in visti:
            continue
        visti.add(chiave)
        eventi.append({
            "id": f"pe-{slug}",
            "nome": nome, "locandina": "", "categoria": categoria(nome), "descrizione": "",
            "provincia": rec.get("provincia", ""), "comune": comune, "indirizzo": indirizzo,
            "riepilogoDate": fmt_periodo(inizio, fine),
            "dataSort": inizio.isoformat(), "dataFine": fine.isoformat(),
            "mapsUrl": "https://www.google.com/maps/search/?api=1&query=" + quote(indirizzo or comune),
            "lat": lat, "lon": lon, "coordApprossimate": approx,
            "instagram": "", "inEvidenza": False,
            "fonte": "portaleventi",
            "url": rec.get("url", url),               # scheda originale: la citazione con link
            "organizzatore": rec.get("organizzatore", ""),
            "telefono": "", "email": "", "sitoWeb": "",
        })

    eventi.sort(key=lambda e: (e["dataSort"], e["nome"]))
    print(f"\nEventi pronti: {len(eventi)} | senza coordinate (non pubblicati): {senza_coord}")
    for e in eventi[:8 if args.dry_run else 3]:
        print(f"  {e['riepilogoDate']:<22} {e['nome'][:55]} ({e['comune'] or '?'}"
              f"{', ' + e['provincia'] if e['provincia'] else ''}){' ~' if e['coordApprossimate'] else ''}")

    if args.dry_run:
        print("\n(dry-run: nessun file scritto)")
        return 0
    # paracadute: se qualcosa e' andato storto non svuoto il file gia' pubblicato
    if not eventi and os.path.exists(F_OUT):
        print("Nessun evento prodotto: lascio invariato il file esistente.")
        salva_json(F_GEO, cache_geo)
        salva_json(F_PAG, nuova_cache)
        return 0
    if scaricate and errori > scaricate / 2:
        print("Troppi errori: non aggiorno l'elenco eventi.")
        return 2
    salva_json(F_OUT, {
        "aggiornato": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fonte": "Portale Eventi",
        "attribuzione": "Titolo, date e luogo: Portale Eventi (portaleventi.it)",
        "eventi": eventi,
    })
    salva_json(F_GEO, cache_geo)
    salva_json(F_PAG, nuova_cache)
    print(f"Salvato {F_OUT} ({int(time.time() - t0)}s totali)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
