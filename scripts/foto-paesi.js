#!/usr/bin/env node
/**
 * foto-paesi.js
 *
 * Per gli eventi che dopo foto-luoghi.js sono ancora senza immagine cerca una FOTO DEL PAESE
 * (il comune dove si svolge l'evento) con licenza libera: Wikipedia -> Wikimedia Commons.
 * La scarica nel tuo repository e la collega all'evento insieme ai crediti (autore + licenza)
 * che la licenza richiede.
 *
 * Uso:  node scripts/foto-paesi.js eventi_sagre.json dati/foto_paesi.json foto-luoghi
 *
 * Come decide, per ogni comune (una ricerca per comune, non per evento):
 *  1. apre la voce di it.wikipedia che ha il nome del comune (es. "Chianni");
 *  2. la accetta solo se NON e' una pagina di disambiguazione e le sue coordinate cadono in
 *     Toscana e vicino all'evento (cosi' non confonde due paesi con lo stesso nome);
 *  3. prende l'immagine principale della voce, scartando stemmi, bandiere, mappe e file .svg;
 *  4. la accetta solo se su Commons ha una licenza libera (CC BY, CC BY-SA, CC0, pubblico dominio);
 *  5. salva la foto (800 px) in foto-luoghi/ e ricorda il risultato in dati/foto_paesi.json,
 *     cosi' le volte successive non rifa' nessuna richiesta.
 *
 * Non sostituisce mai una locandina o una foto del luogo gia' presenti e non fa mai fallire
 * l'aggiornamento degli eventi. Nessuna dipendenza esterna (Node 18+).
 */
'use strict';

const fs = require('fs');
const path = require('path');

const CONFIG = {
  BASE_URL: 'https://stasetoscana.it',
  NOME_CARTELLA_URL: 'foto-luoghi',
  LARGHEZZA: 800,
  GIORNI_RIPROVA: 30, // un paese senza foto viene ricontrollato dopo tanti giorni
  PAUSA_MS: 250,
  MAX_RICERCHE_PER_GIRO: 60,
  MAX_BYTE_FOTO: 1_200_000,
  RAGGIO_KM: 25, // distanza massima tra il centro del comune su Wikipedia e le coordinate dell'evento
  TOSCANA: { latMin: 42.2, latMax: 44.5, lonMin: 9.6, lonMax: 12.5 },
  UA: 'StaseToscana/1.0 (https://stasetoscana.it; stasetoscana@gmail.com) foto-paesi',
};

// ------------------------------------------------------------------ utilita'

const senzaAccenti = (s) => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '');
const norm = (s) => senzaAccenti(s).toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim();
const pausa = (ms) => new Promise((r) => setTimeout(r, ms));

function distanzaM(lat1, lon1, lat2, lon2) {
  const R = 6371000, rad = Math.PI / 180;
  const dLat = (lat2 - lat1) * rad, dLon = (lon2 - lon1) * rad;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

const decodifica = (s) => s.replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#0?39;/g, "'")
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&nbsp;/g, ' ');
const pulisciHtml = (s) => decodifica(String(s || '').replace(/<[^>]*>/g, ' ')).replace(/\s+/g, ' ').trim();

function hashCorto(s) {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
  return h.toString(36);
}

const giorniTra = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 864e5);

// ------------------------------------------------------------------ rete (sostituibile nei test)

async function richiesta(url, tipo) {
  for (let t = 0; t < 3; t++) {
    const res = await fetch(url, {
      headers: { 'User-Agent': CONFIG.UA, Accept: tipo === 'json' ? 'application/json' : 'image/*' },
      signal: AbortSignal.timeout(20000),
    });
    if (res.status === 429 || res.status === 503) {
      await pausa(Math.min(15, Number(res.headers.get('retry-after')) || 5) * 1000);
      continue;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status} ${url.slice(0, 90)}`);
    return tipo === 'json' ? res.json() : Buffer.from(await res.arrayBuffer());
  }
  throw new Error('troppe richieste (429): ' + url.slice(0, 90));
}
const apiReale = { json: (u) => richiesta(u, 'json'), buffer: (u) => richiesta(u, 'img') };

// ------------------------------------------------------------------ ricerca su Wikipedia / Commons

const FILE_SCARTATI = /\.svg$|logo|stemma|bandiera|flag|mappa|map[._ -]|locator|planimetria|coat[ _]of[ _]arms|gonfalone/i;
const FILE_AMMESSI = /\.(jpe?g|png|webp)$/i;
const LICENZE_LIBERE = /^(CC[ -]?BY(-SA)?[ -]?\d|CC0|Public domain|PD[- ]|Pubblico dominio)/i;
const LICENZE_NO = /\b(NC|ND)\b|fair use|non-?free/i;

async function paginaComune(comune, api) {
  for (const titolo of [comune, comune + ' (Italia)']) {
    const url = 'https://it.wikipedia.org/w/api.php?' + new URLSearchParams({
      action: 'query', format: 'json', formatversion: '2', redirects: '1', titles: titolo,
      prop: 'pageimages|coordinates|pageprops', piprop: 'thumbnail|name', pithumbsize: String(CONFIG.LARGHEZZA),
      ppprop: 'disambiguation', colimit: '1',
    });
    const j = await api.json(url);
    const p = j && j.query && j.query.pages && j.query.pages[0];
    if (!p || p.missing) continue;
    if (p.pageprops && Object.prototype.hasOwnProperty.call(p.pageprops, 'disambiguation')) continue;
    return p;
  }
  return null;
}

function paginaAffidabile(p, g) {
  const c = p.coordinates && p.coordinates[0];
  if (!c) return 'la voce non ha coordinate verificabili';
  const T = CONFIG.TOSCANA;
  if (c.lat < T.latMin || c.lat > T.latMax || c.lon < T.lonMin || c.lon > T.lonMax) return 'la voce non e\' in Toscana';
  if (g.lat != null && g.lon != null && distanzaM(g.lat, g.lon, c.lat, c.lon) > CONFIG.RAGGIO_KM * 1000) {
    return 'la voce e\' troppo lontana dall\'evento';
  }
  return '';
}

async function crediti(nomeFile, api) {
  const url = 'https://commons.wikimedia.org/w/api.php?' + new URLSearchParams({
    action: 'query', format: 'json', formatversion: '2', prop: 'imageinfo', iiprop: 'extmetadata',
    iiextmetadatafilter: 'Artist|LicenseShortName|LicenseUrl|NonFree', titles: 'File:' + nomeFile,
  });
  const j = await api.json(url);
  const pg = j && j.query && j.query.pages && j.query.pages[0];
  const m = pg && pg.imageinfo && pg.imageinfo[0] && pg.imageinfo[0].extmetadata;
  if (!m || !m.LicenseShortName) return null; // file non presente su Commons
  const licenza = pulisciHtml(m.LicenseShortName.value);
  if (!LICENZE_LIBERE.test(licenza) || LICENZE_NO.test(licenza)) return null;
  if (m.NonFree && String(m.NonFree.value).toLowerCase() === 'true') return null;
  return {
    autore: (m.Artist && pulisciHtml(m.Artist.value).slice(0, 80)) || 'Autore non indicato',
    licenza,
    licenzaUrl: m.LicenseUrl ? pulisciHtml(m.LicenseUrl.value) : '',
    paginaUrl: 'https://commons.wikimedia.org/wiki/File:' + encodeURIComponent(nomeFile),
  };
}

async function cercaFotoPaese(g, api, oggi, cartella, log) {
  const vuota = { comune: g.comune, assente: true, verificato: oggi };
  const p = await paginaComune(g.comune, api);
  if (!p) { log(`  ✘ ${g.comune}: nessuna voce Wikipedia con questo nome`); return vuota; }
  const problema = paginaAffidabile(p, g);
  if (problema) { log(`  ✘ ${g.comune}: ${problema}`); return vuota; }
  if (!p.thumbnail || !p.thumbnail.source || !p.pageimage) { log(`  ✘ ${g.comune}: la voce non ha un'immagine principale`); return vuota; }
  if (!FILE_AMMESSI.test(p.pageimage) || FILE_SCARTATI.test(p.pageimage)) {
    log(`  ✘ ${g.comune}: l'immagine principale (${p.pageimage}) e' uno stemma, una mappa o un formato non adatto`);
    return vuota;
  }
  const cr = await crediti(p.pageimage, api);
  if (!cr) { log(`  ✘ ${g.comune}: l'immagine principale non ha una licenza libera verificabile`); return vuota; }

  const buf = await api.buffer(p.thumbnail.source);
  if (!buf || !buf.length || buf.length > CONFIG.MAX_BYTE_FOTO) throw new Error('foto vuota o troppo grande');
  const ext = (/\.(jpe?g|png|webp)(\?|$)/i.exec(p.thumbnail.source) || [, 'jpg'])[1].toLowerCase().replace('jpeg', 'jpg');
  const nome = `paese-${norm(g.comune).replace(/ /g, '-').slice(0, 40)}-${hashCorto(p.pageimage)}.${ext}`;
  fs.mkdirSync(cartella, { recursive: true });
  fs.writeFileSync(path.join(cartella, nome), buf);
  log(`  ✔ ${g.comune} -> "${p.title}", ${cr.licenza}`);
  return { comune: g.comune, file: nome, titolo: p.title, ...cr, verificato: oggi };
}

// ------------------------------------------------------------------ applicazione agli eventi

function applica(ev, voce) {
  ev.locandina = `${CONFIG.BASE_URL}/${CONFIG.NOME_CARTELLA_URL}/${voce.file}`;
  ev.fotoCrediti = {
    testo: `Foto del paese (${voce.comune}): ${voce.autore}, ${voce.licenza}, via Wikimedia Commons`,
    url: voce.paginaUrl,
    licenzaUrl: voce.licenzaUrl || '',
  };
  ev.immagineIllustrativa = false;
}

async function processa(dati, cache, api, oggi, cartella, log = console.log) {
  const gruppi = new Map();
  for (const ev of dati.eventi || []) {
    if (ev.locandina) continue;
    const comune = String(ev.comune || '').trim();
    if (!comune) continue;
    const chiave = norm(comune);
    if (!gruppi.has(chiave)) gruppi.set(chiave, { comune, lat: null, lon: null, eventi: [] });
    const g = gruppi.get(chiave);
    if (g.lat == null && ev.lat != null && ev.lon != null) { g.lat = Number(ev.lat); g.lon = Number(ev.lon); }
    g.eventi.push(ev);
  }

  const st = { paesi: gruppi.size, conFoto: 0, daCache: 0, cercati: 0, errori: 0, eventi: 0 };
  for (const [chiave, g] of gruppi) {
    let voce = cache[chiave];
    const scaduta = voce && voce.assente && giorniTra(voce.verificato, oggi) >= CONFIG.GIORNI_RIPROVA;
    if (!voce || scaduta) {
      if (st.cercati >= CONFIG.MAX_RICERCHE_PER_GIRO) { log('  … limite di ricerche raggiunto, si prosegue al prossimo giro'); continue; }
      st.cercati++;
      try {
        voce = await cercaFotoPaese(g, api, oggi, cartella, log);
        cache[chiave] = voce;
      } catch (e) {
        st.errori++;
        log(`  ⚠ ${g.comune}: ${e.message} (verra' riprovato)`);
        continue;
      }
      if (CONFIG.PAUSA_MS) await pausa(CONFIG.PAUSA_MS);
    } else {
      st.daCache++;
    }
    if (voce && voce.file) {
      st.conFoto++;
      g.eventi.forEach((ev) => { applica(ev, voce); st.eventi++; });
    }
  }
  return st;
}

// ------------------------------------------------------------------ main

if (require.main === module) {
  (async () => {
    const [, , eventiPath, cachePath, cartella] = process.argv;
    if (!eventiPath || !cachePath || !cartella) {
      console.error('Uso: node foto-paesi.js <eventi.json> <cache.json> <cartella-foto>');
      process.exit(2);
    }
    let dati;
    try { dati = JSON.parse(fs.readFileSync(eventiPath, 'utf8')); } catch (e) { console.error('Impossibile leggere', eventiPath, e.message); process.exit(1); }
    let cache = {};
    try { cache = JSON.parse(fs.readFileSync(cachePath, 'utf8')); } catch { /* prima esecuzione */ }

    const oggi = new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/Rome' });
    console.log('Foto dei paesi (Wikimedia Commons):');
    const st = await processa(dati, cache, apiReale, oggi, cartella);

    fs.mkdirSync(path.dirname(cachePath), { recursive: true });
    fs.writeFileSync(cachePath, JSON.stringify(cache, null, 1));
    if (st.eventi > 0) fs.writeFileSync(eventiPath, JSON.stringify(dati));
    console.log(`Paesi: ${st.paesi} | con foto: ${st.conFoto} | dalla memoria: ${st.daCache} | cercati ora: ${st.cercati} | errori: ${st.errori}`);
    console.log(`Eventi con foto del paese: ${st.eventi} su ${(dati.eventi || []).length}`);
  })().catch((e) => { console.error('Errore imprevisto (gli eventi restano validi):', e); process.exit(0); });
}

module.exports = { processa, paginaAffidabile, CONFIG };
