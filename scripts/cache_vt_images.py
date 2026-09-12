#!/usr/bin/env python3
"""
Cache delle locandine VisitTuscany direttamente nel repository GitHub.

Cosa fa:
1. Legge eventi_visittuscany.json (gia' filtrato dallo step precedente del workflow)
2. Per ogni evento, se image.url non e' gia' un URL nostro (sito):
   - controlla la cache (vt_image_cache.json) per evitare ricarichi inutili
   - scarica l'immagine da VisitTuscany (con header da browser, per evitare
     il blocco anti-bot del loro WAF, e qualche retry per le connessioni instabili)
   - la salva come file dentro img/vt/ nel repository
   - riscrive image.url con l'URL pubblico sul nostro dominio
3. Rimuove da disco e dalla cache le immagini di eventi non piu' presenti
   (pulizia automatica: gli eventi VT cambiano ogni giorno, cosi' il repo
   non cresce indefinitamente)
4. Se il download di una singola immagine fallisce, lascia l'URL originale
   di VisitTuscany per quell'evento (fallback: non peggiora la situazione attuale)
   e continua con gli altri eventi.

Variabili d'ambiente (opzionali):
  SITE_BASE_URL  dominio pubblico del sito (default: https://stasetoscana.it)

File coinvolti (letti/scritti nella working directory del job, dentro il repo):
  eventi_visittuscany.json   aggiornato in-place con i nuovi URL immagine
  vt_image_cache.json        mappa {url_originale: {path, event_id}}
  img/vt/*                   file immagine scaricati
"""

import json
import os
import time
import hashlib
import mimetypes

import requests

EVENTI_PATH = "eventi_visittuscany.json"
CACHE_PATH = "vt_image_cache.json"
IMG_DIR = "img/vt"

SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "https://stasetoscana.it").rstrip("/")
REQUEST_TIMEOUT = 20  # secondi per il download di ogni singola immagine

HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "https://www.visittuscany.com/",
}


def carica_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fp:
                return json.load(fp)
        except Exception as e:
            print(f"Attenzione: impossibile leggere {path}: {e}")
    return default


def salva_json(path, data):
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False)


def estensione_da_url_o_content_type(url, content_type):
    ext = os.path.splitext(url.split("?")[0])[1]
    if ext and len(ext) <= 5:
        return ext
    guessed = mimetypes.guess_extension(content_type or "") or ".jpg"
    if guessed == ".jpe":
        guessed = ".jpg"
    return guessed


def scarica_immagine(url, tentativi=3):
    for tentativo in range(1, tentativi + 1):
        try:
            r = requests.get(url, timeout=REQUEST_TIMEOUT, headers=HEADERS_BROWSER)
            r.raise_for_status()
            content_type = r.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                print(f"  Salto (non e' un'immagine, content-type={content_type}): {url}")
                return None, None
            return r.content, content_type
        except Exception as e:
            print(f"  Tentativo {tentativo}/{tentativi} fallito per {url}: {e}")
            if tentativo < tentativi:
                time.sleep(2 * tentativo)
    return None, None


def main():
    os.makedirs(IMG_DIR, exist_ok=True)

    dati = carica_json(EVENTI_PATH, {"response": {"data": {"doc": []}}})
    eventi = dati.get("response", {}).get("data", {}).get("doc", [])

    cache = carica_json(CACHE_PATH, {})

    url_correnti = set()
    caricate = 0
    da_cache = 0
    fallite = 0

    for ev in eventi:
        image = ev.get("image") or {}
        url_originale = image.get("url")
        if not url_originale:
            continue

        # Se e' gia' un URL nostro, non tocchiamo nulla
        if url_originale.startswith(SITE_BASE_URL):
            continue

        url_correnti.add(url_originale)

        voce_cache = cache.get(url_originale)
        if voce_cache and voce_cache.get("path") and os.path.exists(voce_cache["path"]):
            image["url"] = f"{SITE_BASE_URL}/{voce_cache['path']}"
            da_cache += 1
            continue

        print(f"Scarico: {url_originale}")
        content, content_type = scarica_immagine(url_originale)
        time.sleep(0.4)  # piccola pausa per non sembrare traffico anomalo al WAF di VT
        if content is None:
            fallite += 1
            continue  # lascia l'URL originale di VisitTuscany come fallback

        ext = estensione_da_url_o_content_type(url_originale, content_type)
        nome_hash = hashlib.sha1(url_originale.encode()).hexdigest()[:12]
        event_id = ev.get("id", "evento")
        filename = f"vt_{event_id}_{nome_hash}{ext}"
        percorso_relativo = f"{IMG_DIR}/{filename}"

        with open(percorso_relativo, "wb") as fp:
            fp.write(content)

        image["url"] = f"{SITE_BASE_URL}/{percorso_relativo}"
        cache[url_originale] = {"path": percorso_relativo, "event_id": event_id}
        caricate += 1

    # Pulizia: rimuove dalla cache (e da disco) le immagini di eventi non piu' presenti
    chiavi_da_rimuovere = [k for k in cache if k not in url_correnti]
    for k in chiavi_da_rimuovere:
        voce = cache.pop(k)
        try:
            if os.path.exists(voce["path"]):
                os.remove(voce["path"])
        except Exception as e:
            print(f"  Attenzione: impossibile eliminare {voce.get('path')}: {e}")

    salva_json(EVENTI_PATH, dati)
    salva_json(CACHE_PATH, cache)

    print("---")
    print(f"Nuove locandine scaricate e salvate nel repo: {caricate}")
    print(f"Locandine gia' in cache (riusate):            {da_cache}")
    print(f"Download falliti (fallback a URL originale VT): {fallite}")
    print(f"Voci di cache rimosse (eventi non piu' presenti): {len(chiavi_da_rimuovere)}")


if __name__ == "__main__":
    main()
