#!/usr/bin/env node
/**
 * foto-luoghi.js
 *
 * Per gli eventi di Firenze senza immagine cerca una FOTO DEL LUOGO con licenza libera
 * (Wikipedia -> Wikimedia Commons), la scarica nel tuo repository e la collega all'evento
 * insieme ai crediti (autore + licenza) che la licenza richiede.
 *
 * Uso:  node scripts/foto-luoghi.js eventi_firenze.json dati/foto_luoghi.json foto-luoghi
 *
 * Come decide:
 *  1. prende il nome del luogo (es. "Teatro della Pergola") e le coordinate dell'evento;
 *  2. cerca su it.wikipedia le voci entro 150 m dalle coordinate;
 *  3. accetta una voce solo se il suo titolo somiglia al nome del luogo;
 *  4. accetta la foto solo se su Commons ha una licenza libera (CC BY, CC BY-SA, CC0, pubblico dominio);
 *  5. salva la foto (500 px) in foto-luoghi/ e ricorda il risultato in dati/foto_luoghi.json,
 *     così le volte successive non rifà nessuna richiesta.
 *
 * Non sostituisce mai una locandina già presente e non fa mai fallire l'aggiornamento degli eventi.
 * Nessuna dipendenza esterna (Node 18+).
 */
'use strict';

const fs = require('fs');
const path = require('path');

const CONFIG = {
  BASE_URL: 'https://stasetoscana.it',
  NOME_CARTELLA_URL: 'foto-luoghi',
  RAGGIO_M: 150,
  LARGHEZZA: 500, // larghezza "standard" delle miniature Wikimedia
  GIORNI_RIPROVA: 30, // un luogo senza foto viene ricontrollato dopo tanti giorni
  PAUSA_MS: 250,
  MAX_RICERCHE_PER_GIRO: 60,
  MAX_BYTE_FOTO: 1_000_000,
  SOGLIA_NOME: 0.6,
  UA: 'StaseToscana/1.0 (https://stasetoscana.it; stasetoscana@gmail.com) foto-luoghi',
};

// ------------------------------------------------------------------ utilità

const senzaAccenti = (s) => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '');
const norm = (s) => senzaAccenti(s).toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim();
const pausa = (ms) => new Promise((r) => setTimeout(r, ms));

const STOP = new Set(['il', 'lo', 'la', 'le', 'gli', 'di', 'del', 'dell', 'dello', 'della', 'dei', 'degli', 'delle',
  'a', 'al', 'alla', 'in', 'e', 'ex', 'da', 'dal', 'dalla', 'con', 'per', 'the', 'of', 'st']);

function token(s) {
  return senzaAccenti(s).toLowerCase().replace(/\([^)]*\)/g, ' ').replace(/[^a-z0-9 ]+/g, ' ')
    .split(/\s+/).filter((w) => w.length > 1 && !STOP.has(w));
}

function nomiCombaciano(luogo, titolo) {
  const A = token(luogo);
  const B = new Set(token(titolo));
  if (!A.length || !B.size) return false;
  let comuni = 0;
  A.forEach((w) => { if (B.has(w)) comuni++; });
  return comuni / Math.min(A.length, B.size) >= CONFIG.SOGLIA_NOME;
}

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

const FILE_SCARTATI = /\.svg$|logo|stemma|bandiera|flag|mappa|map[._ -]|locator|planimetria|coat[ _]of[ _]arms/i;
const FILE_AMMESSI = /\.(jpe?g|png|webp)$/i;
const LICENZE_LIBERE = /^(CC[ -]?BY(-SA)?[ -]?\d|CC0|Public domain|PD[- ]|Pubblico dominio)/i;
const LICENZE_NO = /\b(NC|ND)\b|fair use|non-?free/i;

async function vociVicine(lat, lon, api) {
  const url = 'https://it.wikipedia.org/w/api.php?' + new URLSearchParams({
    action: 'query', format: 'json', formatversion: '2', generator: 'geosearch',
    ggscoord: `${lat}|${lon}`, ggsradius: String(CONFIG.RAGGIO_M), ggslimit: '10',
    prop: 'pageimages|coordinates', piprop: 'thumbnail|name', pithumbsize: String(CONFIG.LARGHEZZA),
    pilimit: '10', colimit: '10',
  });
  const j = await api.json(url);
  return (j && j.query && j.query.pages) || [];
}

function scegliVoce(luogo, lat, lon, pagine) {
  let migliore = null, dMin = Infinity;
  for (const p of pagine) {
    if (!p.thumbnail || !p.thumbnail.source || !p.pageimage) continue;
    if (!FILE_AMMESSI.test(p.pageimage) || FILE_SCARTATI.test(p.pageimage)) continue;
    const c = p.coordinates && p.coordinates[0];
    if (!c) continue;
    const d = distanzaM(lat, lon, c.lat, c.lon);
    if (d > CONFIG.RAGGIO_M || !nomiCombaciano(luogo, p.title)) continue;
    if (d < dMin) { dMin = d; migliore = { ...p, distanza: Math.round(d) }; }
  }
  return migliore;
}

async function crediti(nomeFile, api) {
  const url = 'https://commons.wikimedia.org/w/api.php?' + new URLSearchParams({
    action: 'query', format: 'json', formatversion: '2', prop: 'imageinfo', iiprop: 'extmetadata',
    iiextmetadatafilter: 'Artist|LicenseShortName|LicenseUrl|NonFree', titles: 'File:' + nomeFile,
  });
  const j = await api.json(url);
  const pg = j && j.query && j.query.pages && j.query.pages[0];
  const m = pg && pg.imageinfo && pg.imageinfo[0] && pg.imageinfo[0].extmetadata;
  if (!m || !m.LicenseShortName) return null; // file non presente su Commons (es. locale a it.wikipedia)
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

async function cercaFoto(g, api, oggi, cartella, log) {
  const vuota = { luogo: g.luogo, assente: true, verificato: oggi };
  const voce = scegliVoce(g.luogo, g.lat, g.lon, await vociVicine(g.lat, g.lon, api));
  if (!voce) { log(`  ✘ ${g.luogo}: nessuna voce Wikipedia corrispondente`); return vuota; }
  const cr = await crediti(voce.pageimage, api);
  if (!cr) { log(`  ✘ ${g.luogo}: "${voce.title}" ha una foto senza licenza libera verificabile`); return vuota; }

  const buf = await api.buffer(voce.thumbnail.source);
  if (!buf || !buf.length || buf.length > CONFIG.MAX_BYTE_FOTO) throw new Error('foto vuota o troppo grande');
  const ext = (/\.(jpe?g|png|webp)(\?|$)/i.exec(voce.thumbnail.source) || [, 'jpg'])[1].toLowerCase().replace('jpeg', 'jpg');
  const nome = `${norm(g.luogo).replace(/ /g, '-').slice(0, 40)}-${hashCorto(voce.pageimage)}.${ext}`;
  fs.mkdirSync(cartella, { recursive: true });
  fs.writeFileSync(path.join(cartella, nome), buf);
  log(`  ✔ ${g.luogo} -> "${voce.title}" (${voce.distanza} m), ${cr.licenza}`);
  return { luogo: g.luogo, file: nome, titolo: voce.title, ...cr, verificato: oggi };
}

// ------------------------------------------------------------------ applicazione agli eventi

function estraiLuogo(ev) {
  const ind = String(ev.indirizzo || '').trim();
  const com = String(ev.comune || '').trim();
  if (!ind || ind === com) return '';
  const suffisso = ', ' + com;
  return com && ind.endsWith(suffisso) ? ind.slice(0, -suffisso.length).trim() : ind;
}

function applica(ev, voce) {
  ev.locandina = `${CONFIG.BASE_URL}/${CONFIG.NOME_CARTELLA_URL}/${voce.file}`;
  ev.fotoCrediti = {
    testo: `Foto del luogo: ${voce.autore}, ${voce.licenza}, via Wikimedia Commons`,
    url: voce.paginaUrl,
    licenzaUrl: voce.licenzaUrl || '',
  };
}

async function processa(dati, cache, api, oggi, cartella, log = console.log) {
  const gruppi = new Map();
  for (const ev of dati.eventi || []) {
    if (ev.locandina) continue;
    if (ev.lat == null || ev.lon == null || ev.coordApprossimate) continue;
    const luogo = estraiLuogo(ev);
    if (!luogo) continue;
    const chiave = norm(luogo) + '|' + norm(ev.comune);
    if (!gruppi.has(chiave)) gruppi.set(chiave, { luogo, lat: Number(ev.lat), lon: Number(ev.lon), eventi: [] });
    gruppi.get(chiave).eventi.push(ev);
  }

  const st = { luoghi: gruppi.size, conFoto: 0, daCache: 0, cercati: 0, errori: 0, eventi: 0 };
  for (const [chiave, g] of gruppi) {
    let voce = cache[chiave];
    const scaduta = voce && voce.assente && giorniTra(voce.verificato, oggi) >= CONFIG.GIORNI_RIPROVA;
    if (!voce || scaduta) {
      if (st.cercati >= CONFIG.MAX_RICERCHE_PER_GIRO) { log('  … limite di ricerche raggiunto, si prosegue al prossimo giro'); continue; }
      st.cercati++;
      try {
        voce = await cercaFoto(g, api, oggi, cartella, log);
        cache[chiave] = voce;
      } catch (e) {
        st.errori++;
        log(`  ⚠ ${g.luogo}: ${e.message} (verrà riprovato)`);
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
      console.error('Uso: node foto-luoghi.js <eventi_firenze.json> <cache.json> <cartella-foto>');
      process.exit(2);
    }
    let dati;
    try { dati = JSON.parse(fs.readFileSync(eventiPath, 'utf8')); } catch (e) { console.error('Impossibile leggere', eventiPath, e.message); process.exit(1); }
    let cache = {};
    try { cache = JSON.parse(fs.readFileSync(cachePath, 'utf8')); } catch { /* prima esecuzione */ }

    const oggi = new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/Rome' });
    console.log('Foto dei luoghi (Wikimedia Commons):');
    const st = await processa(dati, cache, apiReale, oggi, cartella);

    fs.mkdirSync(path.dirname(cachePath), { recursive: true });
    fs.writeFileSync(cachePath, JSON.stringify(cache, null, 1));
    if (st.eventi > 0) fs.writeFileSync(eventiPath, JSON.stringify(dati));
    console.log(`Luoghi: ${st.luoghi} | con foto: ${st.conFoto} | dalla memoria: ${st.daCache} | cercati ora: ${st.cercati} | errori: ${st.errori}`);
    console.log(`Eventi con foto del luogo: ${st.eventi} su ${(dati.eventi || []).length}`);
  })().catch((e) => { console.error('Errore imprevisto (gli eventi restano validi):', e); process.exit(0); });
}

module.exports = { processa, nomiCombaciano, estraiLuogo, scegliVoce, CONFIG };
