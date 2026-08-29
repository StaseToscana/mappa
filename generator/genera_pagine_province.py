#!/usr/bin/env python3
"""
Genera automaticamente le pagine statiche per provincia (eventi-<provincia>.html)
e la pagina hub (eventi-in-toscana.html) a partire da eventi.json ed
eventi_visittuscany.json — la STESSA fonte dati che usa index.html.

Perche' esiste: prima queste 10+1 pagine venivano scritte/aggiornate a mano,
con il rischio che si disallineassero dai dati reali mostrati sulla mappa.
Questo script replica in Python la stessa logica di normalizzazione che gia'
gira in JavaScript dentro index.html (normalizzaEventi / normalizzaEventiVT),
cosi' le pagine statiche restano sempre uno specchio fedele della mappa.

Uso:
    python3 genera_pagine_province.py

Si aspetta di trovare, nella cartella da cui viene lanciato (root del repo):
    eventi.json
    eventi_visittuscany.json
    generator/province-config.json   (testi editoriali + immagini per provincia)

Scrive in output, nella root del repo:
    eventi-in-toscana.html
    eventi-<provincia>.html   (uno per ognuna delle 10 province)
"""

import json
import os
import re
import sys
from datetime import date, datetime
from html import escape
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_URL = "https://stasetoscana.it"

PROVINCE_MAP = {
    "AR": "Arezzo", "FI": "Firenze", "GR": "Grosseto", "LI": "Livorno",
    "LU": "Lucca", "MS": "Massa-Carrara", "PI": "Pisa", "PT": "Pistoia",
    "PO": "Prato", "SI": "Siena",
}
PROVINCE_ORDER = ["Arezzo", "Firenze", "Grosseto", "Livorno", "Lucca",
                   "Massa-Carrara", "Pisa", "Pistoia", "Prato", "Siena"]


# ===================== Normalizzazione eventi (porta 1:1 la logica JS) =====================

def normalizza_eventi_stase(data):
    out = []
    for i, e in enumerate(data):
        out.append({
            "id": e.get("id") or f"json_{i}",
            "nome": e.get("nome"),
            "locandina": e.get("locandina") or None,
            "categoria": e.get("categoria") or "Altro",
            "descrizione": e.get("descrizione") or "",
            "provincia": e.get("provincia"),
            "comune": e.get("comune"),
            "indirizzo": e.get("indirizzo"),
            "riepilogoDate": e.get("riepilogo_date"),
            "dataSort": e.get("data_sort"),
            "dataFine": e.get("data_fine"),
            "mapsUrl": e.get("maps_url"),
            "instagram": e.get("instagram") or None,
            "inEvidenza": bool(e.get("in_evidenza")),
            "fonte": "stase",
        })
    return out


def _categoria_da_typology(typology):
    cat = (typology or "Altro")
    low = cat.lower()
    if "concert" in low or "musica" in low:
        return "Concerto"
    if "sagra" in low:
        return "Sagra"
    if "fest" in low and "festival" not in low:
        return "Festa paesana"
    if "fiera" in low or "mercat" in low:
        return "Fiera"
    if "mostra" in low or "expo" in low:
        return "Mostra/Esposizione"
    if "teatr" in low or "cinema" in low or "spettacolo" in low:
        return "Spettacolo teatrale"
    if "sport" in low:
        return "Evento sportivo"
    if "gastro" in low or "food" in low or "vino" in low or "enogastronomia" in low:
        return "Evento gastronomico"
    if "festival" in low:
        return "Festival"
    if "discotec" in low or "serata" in low:
        return "Serata/Discoteca"
    if "rievoc" in low or "palio" in low or "storica" in low:
        return "Rievocazione storica/Palio"
    if "convegn" in low:
        return "Altro"
    return cat if cat else "Altro"


def _fmt_data_it(iso):
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
        return f"{d.day}/{d.month}/{d.year}"
    except Exception:
        return ""


def normalizza_eventi_vt(raw):
    doc = raw.get("response", {}).get("data", {}).get("doc", [])
    out = []
    for i, e in enumerate(doc):
        nome = e.get("title") or e.get("name") or "Evento VisitTuscany"
        url = e.get("url")
        descrizione = e.get("subtitle") or ""
        categoria = _categoria_da_typology(e.get("typology"))

        data_sort = data_fine = None
        riepilogo = ""
        periods = e.get("periods") or []
        if periods:
            p = periods[0]
            data_sort = p.get("startDate")
            data_fine = p.get("endDate") or data_sort
            if data_sort:
                riepilogo = _fmt_data_it(data_sort)
                if data_fine and data_fine != data_sort:
                    riepilogo += " - " + _fmt_data_it(data_fine)

        comune = provincia = indirizzo = None
        location = e.get("location") or {}
        mappa = location.get("map") or {}
        indirizzo = mappa.get("address")

        locality = location.get("locality") or []
        if locality:
            comune = locality[0].get("name") or locality[0].get("locality")
            provincia = locality[0].get("province")

        if not comune and indirizzo:
            for part in reversed(indirizzo.split(",")):
                part = part.strip()
                if part and part != "Italia" and not re.fullmatch(r"[A-Z]{2}", part) and len(part) > 2:
                    comune = part
                    break

        if not provincia and indirizzo:
            m = re.search(r",\s*([A-Z]{2})\s*,", indirizzo)
            if m:
                provincia = m.group(1)
        if provincia and provincia in PROVINCE_MAP:
            provincia = PROVINCE_MAP[provincia]

        locandina = (e.get("image") or {}).get("url")
        in_evidenza = e.get("topevent") in (True, "true")

        out.append({
            "id": "vt_" + str(e.get("id") or i),
            "nome": nome,
            "locandina": locandina,
            "categoria": categoria,
            "descrizione": descrizione,
            "provincia": provincia,
            "comune": comune,
            "indirizzo": indirizzo,
            "riepilogoDate": riepilogo,
            "dataSort": data_sort,
            "dataFine": data_fine,
            "mapsUrl": url,
            "instagram": None,
            "inEvidenza": in_evidenza,
            "fonte": "visittuscany",
        })
    return out


def carica_eventi():
    stase_path = os.path.join(ROOT, "eventi.json")
    vt_path = os.path.join(ROOT, "eventi_visittuscany.json")

    eventi_stase = []
    if os.path.exists(stase_path):
        with open(stase_path, encoding="utf-8") as f:
            eventi_stase = normalizza_eventi_stase(json.load(f))

    eventi_vt = []
    if os.path.exists(vt_path):
        with open(vt_path, encoding="utf-8") as f:
            eventi_vt = normalizza_eventi_vt(json.load(f))

    tutti = eventi_stase + eventi_vt
    visti = set()
    unici = []
    for ev in tutti:
        chiave = (ev.get("nome") or "", ev.get("dataSort") or "", ev.get("comune") or "")
        if chiave in visti:
            continue
        visti.add(chiave)
        unici.append(ev)

    oggi = date.today().isoformat()

    def futuro(ev):
        fine = ev.get("dataFine") or ev.get("dataSort")
        return (fine or "9999-12-31") >= oggi

    futuri = [ev for ev in unici if futuro(ev)]
    futuri.sort(key=lambda ev: ev.get("dataSort") or "9999-12-31")
    return futuri


# ===================== Rendering HTML =====================

def badge_vt(ev):
    return '<span class="badge-vt">VisitTuscany</span>' if ev["fonte"] == "visittuscany" else ""


def scheda_url(ev):
    return f"{SITE_URL}/?id={ev['id']}"


def card_evento_html(ev):
    img = escape(ev.get("locandina") or "", quote=True)
    nome = escape(ev.get("nome") or "")
    categoria = escape(ev.get("categoria") or "Altro")
    comune = escape(ev.get("comune") or "")
    descrizione = escape((ev.get("descrizione") or "")[:220])
    maps = escape(ev.get("mapsUrl") or scheda_url(ev), quote=True)
    return (
        '<article class="card-evento">'
        f'<img src="{img}" alt="{nome}" loading="lazy">'
        '<div class="card-body">'
        f'{badge_vt(ev)}'
        f'<h3>{nome}</h3>'
        f'<span class="categoria">{categoria}</span>'
        f'<p class="luogo">\U0001F4CD {comune}</p>'
        f'<p class="descrizione">{descrizione}</p>'
        '<div class="azioni">'
        f'<a href="{maps}" target="_blank" rel="noopener">Maps</a>'
        f'<a href="{escape(scheda_url(ev), quote=True)}" target="_blank" rel="noopener">Scheda completa</a>'
        '</div></div></article>'
    )


def ld_json_per_provincia(eventi_prov):
    graph = []
    for ev in eventi_prov[:20]:
        item = {
            "@type": "Event",
            "name": ev.get("nome"),
            "startDate": ev.get("dataSort"),
            "endDate": ev.get("dataFine") or ev.get("dataSort"),
            "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
            "eventStatus": "https://schema.org/EventScheduled",
            "location": {
                "@type": "Place",
                "name": f"{ev.get('comune') or ''}, {ev.get('provincia') or ''}".strip(", "),
                "address": ev.get("indirizzo") or "",
            },
        }
        if ev.get("locandina"):
            item["image"] = [ev["locandina"]]
        if ev.get("descrizione"):
            item["description"] = ev["descrizione"][:300]
        graph.append(item)
    return json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)


HEAD_COMUNE = (
    '<link rel="manifest" href="/manifest.json" />'
    '<meta name="theme-color" content="#FF3131" />'
    '<link rel="icon" href="/icon-192.png" />'
    '<link rel="apple-touch-icon" href="/apple-touch-icon.png" />'
    '<meta name="apple-mobile-web-app-capable" content="yes" />'
    '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />'
    '<meta name="apple-mobile-web-app-title" content="Stase Toscana" />'
    '<link href="https://fonts.googleapis.com/css2?family=League+Spartan:wght@400;500;700;900&display=swap" rel="stylesheet" />'
)

CSS_PROVINCIA = """:root{--rosso:#FF3131;--rosso-scuro:#CC0000;--crema:#FFF8EE;--inchiostro:#1B1B1B;--bordo:#F0E6DC;--grigio:#6B6258;--crema-card:#FFFFFF}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"League Spartan",sans-serif;background:var(--crema);color:var(--inchiostro);line-height:1.5}
.top-nav{display:flex;align-items:center;justify-content:space-between;padding:12px 20px;background:white;border-bottom:1px solid var(--bordo);position:sticky;top:0;z-index:50}
.top-nav .nav-logo img{height:40px;width:auto;display:block}
.nav-links{display:flex;align-items:center;gap:8px}
.nav-links a{color:var(--inchiostro);text-decoration:none;font-size:0.82rem;font-weight:700;padding:8px 14px;border-radius:20px;transition:background 0.15s}
.nav-links a:hover{background:var(--crema)}
.nav-links .nav-cta{background:var(--rosso);color:white}
.nav-links .nav-cta:hover{background:var(--rosso-scuro)}
.breadcrumb{padding:14px 20px;background:white;border-bottom:1px solid var(--bordo);font-size:0.78rem;color:var(--grigio)}
.breadcrumb ol{display:flex;align-items:center;gap:6px;list-style:none;max-width:1100px;margin:0 auto;padding:0 20px}
.breadcrumb a{color:var(--rosso);text-decoration:none;font-weight:700}
.breadcrumb a:hover{text-decoration:underline}
.breadcrumb .sep{color:var(--bordo)}
.breadcrumb li[aria-current]{font-weight:700;color:var(--inchiostro)}
.hero-provincia{background:linear-gradient(135deg,var(--rosso),var(--rosso-scuro));padding:48px 20px 40px;text-align:center}
.hero-provincia h1{color:white;font-size:clamp(1.7rem,4vw,2.4rem);font-weight:900;margin-bottom:10px}
.hero-provincia p{color:rgba(255,255,255,0.92);font-size:1rem;max-width:600px;margin:0 auto 18px}
.cta-mappa-provincia{display:inline-block;background:white;color:var(--rosso);text-decoration:none;font-weight:800;font-size:0.88rem;padding:10px 22px;border-radius:24px;transition:transform 0.15s}
.cta-mappa-provincia:hover{transform:translateY(-2px)}
main{max-width:1100px;margin:0 auto;padding:28px 20px 60px}
.layout-provincia{display:grid;grid-template-columns:1fr 260px;gap:28px;align-items:start}
.lista-eventi{min-width:0}
.sidebar{background:white;border-radius:12px;border:1px solid var(--bordo);padding:18px;position:sticky;top:72px}
.sidebar h3{font-size:0.95rem;font-weight:800;margin-bottom:14px;color:var(--inchiostro)}
.altre-province{display:flex;flex-direction:column;gap:4px}
.altre-province a{color:var(--grigio);text-decoration:none;font-size:0.84rem;font-weight:600;padding:7px 10px;border-radius:8px;transition:all 0.15s}
.altre-province a:hover{background:var(--crema);color:var(--rosso)}
.data-header{font-size:1rem;font-weight:700;color:var(--rosso);margin:24px 0 12px;text-transform:uppercase;letter-spacing:0.5px}
.griglia-eventi{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px}
.card-evento{background:white;border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.06);border:1px solid var(--bordo)}
.card-evento img{width:100%;aspect-ratio:4/3;object-fit:cover;display:block}
.card-body{padding:14px}
.card-body h3{font-size:1rem;margin-bottom:6px}
.badge-vt{display:inline-block;background:#0066CC;color:white;font-size:0.62rem;font-weight:700;padding:2px 8px;border-radius:20px;margin-bottom:6px}
.categoria{display:inline-block;font-size:0.7rem;font-weight:700;background:var(--rosso);color:white;padding:2px 10px;border-radius:20px;margin-bottom:8px}
.luogo{font-size:0.82rem;color:var(--grigio);margin-bottom:6px}
.descrizione{font-size:0.82rem;color:#444;margin-bottom:10px;line-height:1.5}
.azioni a{display:inline-block;font-size:0.78rem;font-weight:700;color:var(--rosso);text-decoration:none;margin-right:14px}
.azioni a:hover{text-decoration:underline}
.vuoto{text-align:center;padding:60px 20px;color:var(--grigio)}
.vuoto a{color:var(--rosso);font-weight:700}
.site-footer{background:white;border-top:1px solid var(--bordo);padding:32px 20px}
.footer-inner{max-width:1100px;margin:0 auto;text-align:center}
.footer-inner>p{font-size:0.85rem;color:var(--grigio);margin-bottom:12px}
.footer-links{display:flex;flex-wrap:wrap;justify-content:center;gap:8px 16px}
.footer-links a{color:var(--rosso);text-decoration:none;font-size:0.82rem;font-weight:700}
.footer-links a:hover{text-decoration:underline}
@media(max-width:860px){.layout-provincia{grid-template-columns:1fr}.sidebar{position:static;margin-top:24px}}
@media(max-width:640px){.top-nav{padding:10px 14px}.nav-links a{font-size:0.75rem;padding:6px 10px}.nav-logo img{height:32px}.griglia-eventi{grid-template-columns:1fr}}"""


def top_nav(provincia=None):
    """Nav condivisa. Se 'provincia' e' passata, il link alla mappa porta
    direttamente alla vista gia' filtrata su quella provincia (stesso
    meccanismo ?prov= gia' usato da index.html per filtri condivisibili),
    cosi' il passaggio dalla pagina statica alla mappa non e' un salto a vuoto."""
    mappa_href = f"{SITE_URL}/?prov={quote(provincia)}" if provincia else f"{SITE_URL}/"
    return (
        '<nav class="top-nav">'
        f'<a class="nav-logo" href="{SITE_URL}/"><img src="{SITE_URL}/logo-stase.png" alt="Stase Toscana"></a>'
        '<div class="nav-links">'
        f'<a href="{mappa_href}">Mappa interattiva</a>'
        f'<a href="{SITE_URL}/?tab=aggiungi" class="nav-cta">Pubblica evento</a>'
        '</div></nav>'
    )

FOOTER = (
    '<footer class="site-footer"><div class="footer-inner">'
    '<p>Stase Toscana — Il calendario eventi della Toscana</p>'
    '<div class="footer-links">'
    f'<a href="{SITE_URL}/">Mappa interattiva</a>'
    '<a href="/eventi-in-toscana.html">Eventi per provincia</a>'
    f'<a href="{SITE_URL}/#aggiungi">Pubblica il tuo evento</a>'
    '<a href="https://www.instagram.com/stase.toscana/" target="_blank" rel="noopener">Instagram</a>'
    '</div></div></footer>'
)


def genera_pagina_provincia(nome_provincia, eventi_prov, config):
    info = config[nome_provincia]
    slug = info["slug"]
    n = len(eventi_prov)
    label_n = "Nessun evento" if n == 0 else f"{n} event{'o' if n == 1 else 'i'}"

    breadcrumb = (
        '<nav class="breadcrumb" aria-label="Breadcrumb"><ol>'
        f'<li><a href="{SITE_URL}/">Home</a></li><li class="sep">&rsaquo;</li>'
        '<li><a href="/eventi-in-toscana.html">Province</a></li><li class="sep">&rsaquo;</li>'
        f'<li aria-current="page">{escape(nome_provincia)}</li></ol></nav>'
    )

    mappa_filtrata = f"{SITE_URL}/?prov={quote(nome_provincia)}"
    hero = (
        f'<header class="hero-provincia"><h1>Eventi a {escape(nome_provincia)}</h1>'
        f'<p>{escape(info["desc"])}</p>'
        f'<a href="{mappa_filtrata}" class="cta-mappa-provincia">Vedi su mappa interattiva &rarr;</a>'
        '</header>'
    )

    if eventi_prov:
        gruppi = {}
        ordine = []
        for ev in eventi_prov:
            chiave = ev.get("riepilogoDate") or "Date da definire"
            if chiave not in gruppi:
                gruppi[chiave] = []
                ordine.append(chiave)
            gruppi[chiave].append(ev)
        blocchi = []
        for chiave in ordine:
            cards = "".join(card_evento_html(ev) for ev in gruppi[chiave])
            blocchi.append(f'<h2 class="data-header">{escape(chiave)}</h2><div class="griglia-eventi">{cards}</div>')
        lista_html = "".join(blocchi)
    else:
        lista_html = (
            f'<div class="vuoto">Nessun evento in programma al momento a {escape(nome_provincia)}.<br>'
            f'<a href="{SITE_URL}/#aggiungi">Pubblica il tuo evento</a> o guarda le altre province.</div>'
        )

    altre = "".join(
        f'<a href="/eventi-{config[p]["slug"]}.html">{escape(p)}</a>'
        for p in PROVINCE_ORDER if p != nome_provincia
    )
    sidebar = f'<aside class="sidebar"><h3>Esplora anche</h3><div class="altre-province">{altre}</div></aside>'

    ld_json = ld_json_per_provincia(eventi_prov) if eventi_prov else ""
    ld_json_tag = f'<script type="application/ld+json">{ld_json}</script>' if ld_json else ""

    title = f"Eventi in provincia di {nome_provincia} — Sagre, concerti e feste | Stase Toscana"
    desc_meta = f"Scopri sagre, concerti, fiere e feste in provincia di {nome_provincia}. Elenco sempre aggiornato di {n} eventi in programma."
    url = f"{SITE_URL}/eventi-{slug}.html"

    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{escape(title)}</title>
<meta name="description" content="{escape(desc_meta, quote=True)}" />
<link rel="canonical" href="{url}" />
<meta property="og:type" content="website" />
<meta property="og:title" content="{escape(title, quote=True)}" />
<meta property="og:description" content="{escape(desc_meta, quote=True)}" />
<meta property="og:image" content="{SITE_URL}/logo-stase.png" />
<meta property="og:url" content="{url}" />
{HEAD_COMUNE}
{ld_json_tag}
<style>
{CSS_PROVINCIA}
</style>
</head>
<body>
{top_nav(nome_provincia)}{breadcrumb}{hero}
<main><div class="layout-provincia">
<div class="lista-eventi">{lista_html}</div>
{sidebar}
</div></main>
{FOOTER}
</body>
</html>"""


CSS_HUB_EXTRA = """.hero{background:linear-gradient(135deg,var(--rosso),var(--rosso-scuro));padding:60px 20px 50px;text-align:center;position:relative;overflow:hidden}
.hero::before{content:"";position:absolute;top:-50%;right:-20%;width:300px;height:300px;background:rgba(255,255,255,0.06);border-radius:50%}
.hero img{height:72px;width:auto;display:block;margin:0 auto 18px;position:relative;z-index:1}
.hero h1{color:white;font-size:clamp(1.9rem,5vw,2.8rem);font-weight:900;margin-bottom:10px;position:relative;z-index:1;line-height:1.1}
.hero p{color:rgba(255,255,255,0.92);font-size:1.05rem;max-width:560px;margin:0 auto 24px;position:relative;z-index:1}
.cta-primario{display:inline-block;background:white;color:var(--rosso);text-decoration:none;font-weight:800;font-size:0.95rem;padding:12px 28px;border-radius:30px;box-shadow:0 4px 16px rgba(0,0,0,0.15);transition:transform 0.15s,box-shadow 0.15s;position:relative;z-index:1}
.cta-primario:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(0,0,0,0.2)}
.intro-seo{background:white;padding:40px 20px;border-bottom:1px solid var(--bordo)}
.intro-seo-inner{max-width:720px;margin:0 auto;text-align:center}
.intro-seo h2{font-size:1.35rem;font-weight:800;margin-bottom:14px;color:var(--inchiostro)}
.intro-seo p{font-size:0.95rem;color:var(--grigio);line-height:1.7}
.griglia{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:20px;margin-bottom:48px}
.card-provincia{display:block;background:var(--crema-card);border-radius:16px;overflow:hidden;text-decoration:none;color:inherit;box-shadow:0 2px 8px rgba(0,0,0,0.06);border:1px solid var(--bordo);transition:transform 0.2s,box-shadow 0.2s}
.card-provincia:hover{transform:translateY(-4px);box-shadow:0 8px 24px rgba(0,0,0,0.1);border-color:var(--rosso)}
.card-img-wrap{position:relative;height:180px;overflow:hidden;background:linear-gradient(135deg,#f0e6dc,#e8ddd0)}
.card-provincia img{width:100%;height:100%;object-fit:cover;display:block;transition:transform 0.3s}
.card-provincia:hover img{transform:scale(1.05)}
.card-overlay{position:absolute;inset:0;background:linear-gradient(to top,rgba(27,27,27,0.75) 0%,rgba(27,27,27,0.2) 50%,transparent 100%);display:flex;flex-direction:column;justify-content:flex-end;padding:16px}
.card-overlay h2{color:white;font-size:1.35rem;font-weight:800;margin-bottom:6px}
.badge{display:inline-block;background:var(--rosso);color:white;font-size:0.72rem;font-weight:700;padding:3px 10px;border-radius:20px;width:fit-content}
.card-body{padding:14px 16px 18px}
.card-desc{font-size:0.88rem;color:var(--grigio);line-height:1.5;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
@media(max-width:640px){.hero{padding:40px 16px 36px}.intro-seo{padding:28px 16px}.griglia{grid-template-columns:1fr}}"""


def genera_pagina_hub(conteggi, config):
    cards = []
    for nome in PROVINCE_ORDER:
        info = config[nome]
        n = conteggi.get(nome, 0)
        label = "Nessun evento" if n == 0 else f"{n} event{'o' if n == 1 else 'i'}"
        cards.append(
            f'<a class="card-provincia" href="/eventi-{info["slug"]}.html">'
            f'<div class="card-img-wrap"><img src="{escape(info["img"], quote=True)}" alt="{escape(nome)}" loading="lazy">'
            f'<div class="card-overlay"><h2>{escape(nome)}</h2><span class="badge">{label}</span></div></div>'
            f'<div class="card-body"><p class="card-desc">{escape(info["desc"])}</p></div></a>'
        )
    griglia = "".join(cards)

    title = "Eventi in Toscana per provincia | Stase Toscana"
    desc_meta = "Scopri sagre, concerti, fiere e feste in tutte le province della Toscana. Mappa aggiornata con eventi a Firenze, Siena, Pisa, Lucca, Arezzo e altre province."
    url = f"{SITE_URL}/eventi-in-toscana.html"

    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title><meta name="description" content="{escape(desc_meta, quote=True)}"><link rel="canonical" href="{url}"><meta property="og:type" content="website"><meta property="og:title" content="{escape(title, quote=True)}"><meta property="og:description" content="Sagre, concerti, fiere e feste in tutte le province toscane."><meta property="og:image" content="{SITE_URL}/logo-stase.png"><meta property="og:url" content="{url}">{HEAD_COMUNE}<style>{CSS_PROVINCIA}
{CSS_HUB_EXTRA}</style></head><body>{top_nav()}<header class="hero"><a href="{SITE_URL}/"><img src="{SITE_URL}/logo-stase.png" alt="Stase Toscana"></a><h1>Scopri gli eventi in Toscana</h1><p>Sagre, concerti, fiere e feste in tutte le province toscane. Scegli la tua destinazione e trova subito cosa fare.</p><a href="{SITE_URL}/" class="cta-primario">Esplora la mappa interattiva</a></header><section class="intro-seo"><div class="intro-seo-inner"><h2>Eventi in Toscana: cosa fare oggi, domani e nel weekend</h2><p>Stase Toscana è il portale dedicato agli eventi della Toscana. Ogni giorno raccogliamo sagre, concerti, festival, fiere, mercatini, mostre ed eventi sportivi in tutte le province: Firenze, Siena, Pisa, Lucca, Arezzo, Livorno, Pistoia, Prato, Grosseto e Massa-Carrara. Gli organizzatori possono pubblicare gratuitamente i propri appuntamenti, che compaiono in tempo reale sulla mappa interattiva e nelle pagine provinciali. Che tu stia cercando una sagra di paese, un concerto all'aperto o una fiera enogastronomica, qui trovi l'evento giusto per ogni occasione.</p></div></section><main><div class="griglia">{griglia}</div></main>{FOOTER}</body></html>"""


# ===================== Main =====================

def main():
    config_path = os.path.join(ROOT, "generator", "province-config.json")
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    eventi = carica_eventi()

    per_provincia = {p: [] for p in PROVINCE_ORDER}
    for ev in eventi:
        p = ev.get("provincia")
        if p in per_provincia:
            per_provincia[p].append(ev)

    conteggi = {p: len(evs) for p, evs in per_provincia.items()}

    for nome in PROVINCE_ORDER:
        html = genera_pagina_provincia(nome, per_provincia[nome], config)
        slug = config[nome]["slug"]
        out_path = os.path.join(ROOT, f"eventi-{slug}.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Scritta eventi-{slug}.html ({conteggi[nome]} eventi)")

    hub_html = genera_pagina_hub(conteggi, config)
    with open(os.path.join(ROOT, "eventi-in-toscana.html"), "w", encoding="utf-8") as f:
        f.write(hub_html)
    print(f"Scritta eventi-in-toscana.html (totale {sum(conteggi.values())} eventi)")


if __name__ == "__main__":
    main()
