#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stase Toscana - scraper per Sagre Autentiche (https://sagreautentiche.it), sola Toscana.

Copia SOLO: titolo, date, luogo, coordinate, provincia.
NON copia: descrizioni, immagini, programmi.

Come funziona (ed e' il motivo per cui questo scraper e' il piu' leggero dei quattro):
la pagina /esplora/toscana/ porta dentro di se' un JSON con TUTTI gli eventi della
regione, con coordinate, comune, sigla della provincia e date gia' separate. Quindi
basta UNA richiesta al giorno: niente paginazione, niente lettura delle singole
schede, niente geocoding.

Note:
- il campo "tag" vale "uncertain" su quasi tutti gli eventi: sono date da
  confermare. Vengono pubblicati con l'avviso "date indicative" (il sito ha gia'
  il modo di mostrarlo), esattamente come si fa con Terre di Pisa.
- non c'e' una pagina di condizioni d'uso sul sito (verificato: solo privacy e
  cookie policy su Iubenda) e robots.txt e' aperto. Restiamo comunque sui fatti e
  citiamo sempre la fonte con il link alla scheda originale.

Uso:
  python scripts/scrape_sagreautentiche.py --dry-run
  python scripts/scrape_sagreautentiche.py
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from urllib.parse import quote
from urllib.robotparser import RobotFileParser

import requests

BASE = "https://sagreautentiche.it"
LISTA = BASE + "/esplora/toscana/"
UA = "StaseToscanaBot/1.0 (+https://stasetoscana.it)"
PAUSA = 1.5          # secondi tra due richieste
MIN_RICONOSCIUTI = 0.9   # quota minima di eventi della pagina che il regex deve riconoscere
MIN_RISPETTO_PRECEDENTE = 0.5   # se gli eventi crollano sotto questa quota rispetto a ieri, non si aggiorna
MAX_GIORNI = 40      # scarta eventi piu' lunghi di cosi' (rassegne, stagioni intere)
F_OUT = "eventi_sagreautentiche.json"

MESI = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
MESI_NUM = {"gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
            "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12}

PROVINCE = {
    "AR": "Arezzo", "FI": "Firenze", "GR": "Grosseto", "LI": "Livorno", "LU": "Lucca",
    "MS": "Massa-Carrara", "PI": "Pisa", "PT": "Pistoia", "PO": "Prato", "SI": "Siena",
}

# Categorie: stesse regole degli altri scraper, cosi' la tassonomia resta una sola.
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

# Un evento nel payload: campi stringa con possibili virgolette escapate (\"), quindi
# il gruppo cattura tutto tranne il carattere di chiusura. "geo" e' un oggetto JSON
# a un solo livello di profondita': si ferma alla prima graffa chiusa.
RE_OGGETTO = re.compile(
    r'\{"id":(\d+),"title":"((?:[^"\\]|\\.)*)","excerpt":"((?:[^"\\]|\\.)*)"'
    r',"permalink":"((?:[^"\\]|\\.)*)","geo":(\{.*?\})'
    r',"image":"((?:[^"\\]|\\.)*)","date":"((?:[^"\\]|\\.)*)","tag":"((?:[^"\\]|\\.)*)"',
    re.S,
)
RE_DATA_IT = re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")


# ----------------------------------------------------------------------------- rete

S = requests.Session()
S.headers["User-Agent"] = UA
ROBOTS = RobotFileParser()
_ultimo = 0.0


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
    try:
        testo = get(BASE + "/robots.txt").text
    except Exception as e:
        print(f"! robots.txt non leggibile ({e}): procedo con prudenza")
        ROBOTS.parse([])
        return
    ROBOTS.parse(testo.splitlines())


# ------------------------------------------------------------------------ parsing

def _j(s):
    """Riporta una stringa JSON escapata al suo valore vero."""
    try:
        return json.loads('"' + s + '"')
    except Exception:
        return s


def _data_testo(t):
    """'05 Novembre 2026' -> date(2026, 11, 5). None se non si capisce."""
    m = RE_DATA_IT.search(t or "")
    if not m:
        return None
    mese = MESI_NUM.get(m.group(2).lower())
    if not mese:
        return None
    try:
        return date(int(m.group(3)), mese, int(m.group(1)))
    except ValueError:
        return None


def periodi_da_testo(t):
    """'05 Novembre 2026 - 15 Novembre 2026' -> (inizio, fine). Una data sola -> fine = inizio."""
    trovate = RE_DATA_IT.findall(t or "")
    inizio = _data_testo(t)
    if not inizio:
        return None, None
    fine = inizio
    if len(trovate) > 1:
        seconda = _data_testo(" ".join(trovate[-1]))
        if seconda and seconda >= inizio:
            fine = seconda
    return inizio, fine


def categoria(titolo):
    import unicodedata
    t = "".join(c for c in unicodedata.normalize("NFD", titolo.lower()) if unicodedata.category(c) != "Mn")
    for nome, regola in _REGOLE_COMPILATE:
        if regola.search(t):
            return nome
    return "Sagra"          # il catalogo e' di sagre: e' il ripiego naturale


def fmt_periodo(dal, al):
    if dal == al:
        return f"{dal.day} {MESI[dal.month - 1]} {dal.year}"
    if (dal.year, dal.month) == (al.year, al.month):
        return f"{dal.day}\u2013{al.day} {MESI[al.month - 1]} {al.year}"
    return f"{dal.day} {MESI[dal.month - 1]} \u2013 {al.day} {MESI[al.month - 1]} {al.year}"


def pulisci_indirizzo(geo):
    """'Chianni, PI, Italia' -> 'Chianni' (comune e provincia hanno un campo loro)."""
    ind = (geo.get("address") or "").strip()
    ind = re.sub(r",?\s*Italia\s*$", "", ind, flags=re.I)
    return ind.strip().strip(",")


# ---------------------------------------------------------------------------- main

def salva_json(path, dati):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, indent=1, sort_keys=True)


def main():
    sys.stdout.reconfigure(line_buffering=True)
    t0 = time.time()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="non scrive nessun file")
    args = ap.parse_args()
    oggi = date.today()

    carica_robots()
    if not ROBOTS.can_fetch(UA, LISTA):
        print(f"robots.txt vieta {LISTA}: non procedo.")
        return 1

    print(f"Leggo l'elenco Toscana da {LISTA}")
    try:
        html = get(LISTA).text
    except Exception as e:
        print(f"! elenco non leggibile: {e}")
        return 1

    oggetti = RE_OGGETTO.findall(html)
    print(f"Eventi trovati nell'elenco: {len(oggetti)}")
    if not oggetti:
        print("Nessun evento riconosciuto: la pagina potrebbe essere cambiata. Non aggiorno nulla.")
        return 1
    # il regex pretende un ordine preciso dei campi: se la pagina contiene molti piu'
    # eventi di quelli riconosciuti, alcuni stanno venendo scartati in silenzio
    presenti = html.count('"permalink":"')
    if presenti and len(oggetti) < presenti * MIN_RICONOSCIUTI:
        print(f"! riconosciuti solo {len(oggetti)} eventi su {presenti} presenti nella pagina "
              f"(<{MIN_RICONOSCIUTI:.0%}): il formato potrebbe essere cambiato. Non aggiorno nulla.")
        return 1

    eventi, scartati_lunghi, scartati_senza_data, scartati_finiti, visti = [], 0, 0, 0, set()
    for (_, titolo, _excerpt, permalink, geo_txt, _image, date_txt, tag) in oggetti:
        titolo = _j(titolo).strip()
        permalink = _j(permalink).strip()
        date_txt = _j(date_txt)
        tag = _j(tag)
        try:
            geo = json.loads(geo_txt)
        except Exception:
            geo = {}
        if not titolo or not permalink:
            continue
        inizio, fine = periodi_da_testo(date_txt)
        if not inizio:
            scartati_senza_data += 1
            continue
        if fine < oggi:
            scartati_finiti += 1
            continue
        if (fine - inizio).days > MAX_GIORNI:
            scartati_lunghi += 1
            continue

        sigla = (geo.get("state_short") or "").strip().upper()
        provincia = PROVINCE.get(sigla)
        if not provincia:
            # se non e' una provincia toscana non e' roba nostra
            continue
        comune = (geo.get("city") or geo.get("name") or "").strip()
        lat, lon = geo.get("lat"), geo.get("lng")
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            lat = lon = None

        slug = permalink.rstrip("/").split("/")[-1]
        chiave = (titolo.lower(), inizio.isoformat(), comune.lower())
        if chiave in visti:
            continue
        visti.add(chiave)

        indirizzo = pulisci_indirizzo(geo) or comune
        eventi.append({
            "id": f"sa-{slug}",
            "nome": titolo, "locandina": "", "categoria": categoria(titolo), "descrizione": "",
            "provincia": provincia, "comune": comune, "indirizzo": indirizzo,
            "riepilogoDate": fmt_periodo(inizio, fine),
            "dataSort": inizio.isoformat(), "dataFine": fine.isoformat(),
            "mapsUrl": (f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
                        if lat is not None else
                        "https://www.google.com/maps/search/?api=1&query=" + quote(indirizzo)),
            "lat": lat, "lon": lon, "coordApprossimate": False,
            "instagram": "", "inEvidenza": False,
            "fonte": "sagreautentiche",
            "url": permalink,                     # scheda originale: la citazione con link
            "dateDaVerificare": (tag == "uncertain"),   # quasi tutti: date da confermare
            "telefono": "", "email": "", "sitoWeb": "",
        })

    eventi.sort(key=lambda e: (e["dataSort"], e["nome"]))
    print(f"Eventi pronti: {len(eventi)} | finiti: {scartati_finiti} | oltre {MAX_GIORNI} giorni: {scartati_lunghi} "
          f"| senza data: {scartati_senza_data} | con date da confermare: {sum(1 for e in eventi if e['dateDaVerificare'])}")
    for e in eventi[:8 if args.dry_run else 3]:
        print(f"  {e['riepilogoDate']:<22} {e['nome'][:48]} ({e['comune']}, {e['provincia']})"
              f"{' DATE?' if e['dateDaVerificare'] else ''}")

    # se gli eventi crollano rispetto all'ultimo file pubblicato, qualcosa non va
    precedente = 0
    if os.path.exists(F_OUT):
        try:
            with open(F_OUT, encoding="utf-8") as f:
                precedente = len(json.load(f).get("eventi", []))
        except Exception:
            precedente = 0
    if precedente >= 20 and len(eventi) < precedente * MIN_RISPETTO_PRECEDENTE:
        print(f"! eventi passati da {precedente} a {len(eventi)}: calo anomalo. Non aggiorno nulla.")
        return 1

    if args.dry_run:
        print("\n(dry-run: nessun file scritto)")
        return 0
    # paracadute: se la pagina cambia e non riconosciamo piu' niente, non svuotare il file
    if not eventi and os.path.exists(F_OUT):
        print("Nessun evento prodotto: lascio invariato il file esistente.")
        return 0
    salva_json(F_OUT, {
        "aggiornato": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fonte": "Sagre Autentiche",
        "attribuzione": "Titolo, date e luogo: Sagre Autentiche (sagreautentiche.it)",
        "eventi": eventi,
    })
    print(f"Salvato {F_OUT} ({int(time.time() - t0)}s totali)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
