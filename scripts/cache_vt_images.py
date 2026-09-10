#!/usr/bin/env python3
"""
Cache delle locandine VisitTuscany su Google Drive.

Cosa fa:
1. Legge eventi_visittuscany.json (gia' filtrato dallo step precedente del workflow)
2. Per ogni evento, se image.url non e' gia' un URL Drive nostro:
   - controlla la cache (vt_image_cache.json) per evitare ricarichi inutili
   - scarica l'immagine da VisitTuscany
   - la carica sulla cartella Google Drive condivisa con l'account di servizio
   - riscrive image.url con l'URL Drive (hotlink-friendly)
3. Rimuove dalla cartella Drive e dalla cache le immagini di eventi non piu' presenti
   (pulizia automatica: gli eventi VT cambiano ogni giorno)
4. Se il download o l'upload di una singola immagine fallisce, lascia l'URL originale
   di VisitTuscany per quell'evento (fallback: non peggiora la situazione attuale)
   e continua con gli altri eventi.

Variabili d'ambiente richieste:
  GDRIVE_SA_JSON    percorso al file JSON dell'account di servizio (decodificato dal secret)
  GDRIVE_FOLDER_ID  ID della cartella Google Drive di destinazione

File coinvolti (letti/scritti nella working directory del job):
  eventi_visittuscany.json   aggiornato in-place con i nuovi URL immagine
  vt_image_cache.json        mappa {url_originale: {file_id, url, event_id}}
"""

import json
import os
import sys
import io
import hashlib
import mimetypes

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]

EVENTI_PATH = "eventi_visittuscany.json"
CACHE_PATH = "vt_image_cache.json"

REQUEST_TIMEOUT = 20  # secondi per il download di ogni singola immagine


def carica_drive_service():
    sa_path = os.environ["GDRIVE_SA_JSON"]
    creds = service_account.Credentials.from_service_account_file(sa_path, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


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


def scarica_immagine(url):
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "StaseToscana-bot/1.0"})
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "")
        if not content_type.startswith("image/"):
            print(f"  Salto (non e' un'immagine, content-type={content_type}): {url}")
            return None, None
        return r.content, content_type
    except Exception as e:
        print(f"  Download fallito per {url}: {e}")
        return None, None


def carica_su_drive(service, folder_id, filename, content, content_type):
    file_metadata = {"name": filename, "parents": [folder_id]}
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=content_type, resumable=False)
    file = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    file_id = file["id"]

    # Rende il file leggibile da chiunque abbia il link (necessario per l'hotlink pubblico)
    service.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
    ).execute()

    return file_id


def url_pubblico(file_id):
    # Formato stabile e hotlink-friendly per immagini ospitate su Drive
    return f"https://lh3.googleusercontent.com/d/{file_id}=w1000"


def elimina_da_drive(service, file_id):
    try:
        service.files().delete(fileId=file_id).execute()
    except Exception as e:
        print(f"  Attenzione: impossibile eliminare il file {file_id} da Drive: {e}")


def main():
    folder_id = os.environ["GDRIVE_FOLDER_ID"]
    service = carica_drive_service()

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

        # Se e' gia' un URL nostro (Drive), non tocchiamo nulla
        if "lh3.googleusercontent.com" in url_originale:
            continue

        url_correnti.add(url_originale)

        voce_cache = cache.get(url_originale)
        if voce_cache and voce_cache.get("url"):
            image["url"] = voce_cache["url"]
            da_cache += 1
            continue

        print(f"Scarico e carico: {url_originale}")
        content, content_type = scarica_immagine(url_originale)
        if content is None:
            fallite += 1
            continue  # lascia l'URL originale di VisitTuscany come fallback

        ext = estensione_da_url_o_content_type(url_originale, content_type)
        nome_hash = hashlib.sha1(url_originale.encode()).hexdigest()[:12]
        event_id = ev.get("id", "evento")
        filename = f"vt_{event_id}_{nome_hash}{ext}"

        try:
            file_id = carica_su_drive(service, folder_id, filename, content, content_type)
        except Exception as e:
            print(f"  Upload su Drive fallito per {url_originale}: {e}")
            fallite += 1
            continue

        nuovo_url = url_pubblico(file_id)
        image["url"] = nuovo_url
        cache[url_originale] = {"file_id": file_id, "url": nuovo_url, "event_id": event_id}
        caricate += 1

    # Pulizia: rimuove dalla cache (e da Drive) le immagini di eventi non piu' presenti
    chiavi_da_rimuovere = [k for k in cache if k not in url_correnti]
    for k in chiavi_da_rimuovere:
        voce = cache.pop(k)
        elimina_da_drive(service, voce["file_id"])

    salva_json(EVENTI_PATH, dati)
    salva_json(CACHE_PATH, cache)

    print("---")
    print(f"Nuove locandine caricate su Drive: {caricate}")
    print(f"Locandine gia' in cache (riusate):  {da_cache}")
    print(f"Download/upload falliti (fallback a URL originale VT): {fallite}")
    print(f"Voci di cache rimosse (eventi non piu' presenti):      {len(chiavi_da_rimuovere)}")


if __name__ == "__main__":
    main()
