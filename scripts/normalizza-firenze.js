#!/usr/bin/env node
/**
 * normalizza-firenze.js
 *
 * Legge il GeoJSON "Eventi a Firenze" (Comune di Firenze / FeelFlorence, CC-BY 4.0)
 * e produce eventi_firenze.json nel formato usato da Stase Toscana.
 *
 * Uso:  node scripts/normalizza-firenze.js <input.geojson> <output.json>
 * Test: OGGI_OVERRIDE=2026-10-11 node scripts/normalizza-firenze.js sample.geojson out.json
 *
 * Nessuna dipendenza esterna (Node 18+).
 *
 * ASSUNZIONE DA VERIFICARE: dataSort e dataFine sono scritti come 'YYYY-MM-DD'.
 * Se nel tuo index.html usi un altro formato, cambia solo la funzione fmtSort().
 */
'use strict';

const fs = require('fs');

const [, , inPath, outPath] = process.argv;
if (!inPath || !outPath) {
  console.error('Uso: node normalizza-firenze.js <input.geojson> <output.json>');
  process.exit(2);
}

const CONFIG = {
  // Eventi "continui" (mostre, rassegne senza date puntuali) più lunghi di così vengono scartati,
  // come già fai per VisitTuscany. Gli eventi con date puntuali o ricorrenti NON sono toccati.
  MAX_DURATA_CONTINUI_GG: 40,
  MAX_DESCRIZIONE: 400,
  // Il file contiene, per ogni evento con date puntuali o ricorrenti, tutte le date dei prossimi N giorni
  // (serve ai contenuti social/caroselli settimanali, che guardano anche settimane future).
  ORIZZONTE_OCCORRENZE_GG: 60,
  MAX_OCCORRENZE_PER_EVENTO: 60,
  // Se il nuovo file ha meno del 30% degli eventi del precedente, il job fallisce
  // (protegge da download parziali o cambi di formato).
  SOGLIA_CALO_ANOMALO: 0.3,
  CENTRO_FIRENZE: { lat: 43.7696, lon: 11.2558 },
  // Riquadro approssimativo della Toscana, per scartare coordinate palesemente sbagliate.
  BBOX: { latMin: 42.2, latMax: 44.5, lonMin: 9.6, lonMax: 12.5 },
  FONTE: 'Comune di Firenze – FeelFlorence',
  LICENZA: 'CC BY 4.0',
  LICENZA_URL: 'https://creativecommons.org/licenses/by/4.0/deed.it',
  ATTRIBUZIONE: 'Dati: Comune di Firenze – FeelFlorence.it (CC BY 4.0)',
};

// ---------------------------------------------------------------- date (stringhe ISO)

const OGGI =
  process.env.OGGI_OVERRIDE ||
  new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/Rome' }); // YYYY-MM-DD

const NOMI_GIORNI = ['domenica', 'lunedì', 'martedì', 'mercoledì', 'giovedì', 'venerdì', 'sabato'];
const GIORNI = { domenica: 0, lunedi: 1, martedi: 2, mercoledi: 3, giovedi: 4, venerdi: 5, sabato: 6 };

const senzaAccenti = (s) => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '');
const toUTC = (iso) => { const [y, m, d] = iso.split('-').map(Number); return Date.UTC(y, m - 1, d); };
const addDays = (iso, n) => new Date(toUTC(iso) + n * 864e5).toISOString().slice(0, 10);
const diffDays = (a, b) => Math.round((toUTC(b) - toUTC(a)) / 864e5);
const weekday = (iso) => new Date(toUTC(iso)).getUTCDay();
const fmt = (iso) => { const [y, m, d] = iso.split('-'); return `${d}/${m}/${y}`; };
const fmtSort = (iso) => iso; // <- cambia qui se il tuo dataSort non è YYYY-MM-DD
const pad5 = (h) => (h.length === 4 ? '0' + h : h);

function parseIT(s) {
  const m = /(\d{2})\/(\d{2})\/(\d{4})/.exec(s || '');
  return m ? `${m[3]}-${m[2]}-${m[1]}` : null;
}

// ---------------------------------------------------------------- parser del campo "appuntamenti"
/*
 * Esempi reali del formato:
 *   Quando:
 *   Da 01/01/2026 a 30/12/2026:
 *    - Domenica dalle 21:15
 *    - Lunedì dalle 21:15
 *   Dove:
 *   Ex Chiesa di Santa Monaca
 *
 *   Quando:
 *   17/01/2026 dalle 20:30 alle 21:50
 *   31/01/2026 dalle 20:30 alle 21:50
 *   27/09/2026: tutto il giorno
 *   Da 07/01/2026 a 19/01/2038         <- periodo continuo, senza giorni
 */
function parseAppuntamenti(testo) {
  const righe = String(testo || '').split(/\r?\n/).map((r) => r.trim());
  const date = [];
  const periodi = [];
  const luoghi = [];
  let inDove = false;

  for (let i = 0; i < righe.length; i++) {
    const r = righe[i];

    if (/^dove:/i.test(r)) { inDove = true; continue; }
    if (/^quando:/i.test(r)) { inDove = false; continue; }
    if (inDove) {
      if (r === '') inDove = false;
      else if (!luoghi.includes(r)) luoghi.push(r);
      continue;
    }

    const range = /^da\s+(\d{2}\/\d{2}\/\d{4})\s+a\s+(\d{2}\/\d{2}\/\d{4})/i.exec(r);
    if (range) {
      const per = { da: parseIT(range[1]), a: parseIT(range[2]), giorni: new Set(), ora: null, orePerGiorno: {} };
      let j = i + 1;
      while (j < righe.length && /^-\s*/.test(righe[j])) {
        const mg = /^-\s*([A-Za-zÀ-ÿ]+)/.exec(righe[j]);
        let g;
        if (mg) {
          g = GIORNI[senzaAccenti(mg[1]).toLowerCase()];
          if (g !== undefined) per.giorni.add(g);
        }
        const mo = /dalle\s+(\d{1,2}:\d{2})/i.exec(righe[j]);
        if (mo) {
          if (!per.ora) per.ora = pad5(mo[1]);
          if (g !== undefined) per.orePerGiorno[g] = pad5(mo[1]); // ogni giorno può avere il suo orario
        }
        j++;
      }
      i = j - 1;
      periodi.push(per);
      continue;
    }

    const singola = /^(\d{2}\/\d{2}\/\d{4})(.*)$/.exec(r);
    if (singola) {
      const mo = /dalle\s+(\d{1,2}:\d{2})/i.exec(singola[2]);
      date.push({ iso: parseIT(singola[1]), ora: mo ? pad5(mo[1]) : null });
    }
  }
  return { date, periodi, luoghi };
}

// ---------------------------------------------------------------- scelta della prossima occorrenza

const byIsoOra = (a, b) =>
  a.iso < b.iso ? -1 : a.iso > b.iso ? 1 : (a.ora || '') < (b.ora || '') ? -1 : (a.ora || '') > (b.ora || '') ? 1 : 0;

function calcolaOccorrenza(parsed, inizioFeat, fineFeat) {
  const dateFuture = parsed.date.filter((d) => d.iso && d.iso >= OGGI).sort(byIsoOra);
  const cand = dateFuture.map((d) => ({ tipo: 'date', iso: d.iso, ora: d.ora }));

  for (const per of parsed.periodi) {
    if (!per.da || !per.a || per.a < OGGI) continue;
    const start = per.da > OGGI ? per.da : OGGI;
    const giorni = [...per.giorni];

    // Nessun giorno indicato, oppure quasi tutti i giorni => lo trattiamo come evento continuo.
    if (giorni.length === 0 || giorni.length >= 5) {
      cand.push({ tipo: 'continuo', iso: start, ora: per.ora, da: per.da, a: per.a, durata: diffDays(per.da, per.a) + 1 });
    } else {
      for (let k = 0; k < 7; k++) {
        const d = addDays(start, k);
        if (d > per.a) break;
        if (per.giorni.has(weekday(d))) {
          cand.push({ tipo: 'ricorrente', iso: d, ora: per.orePerGiorno[weekday(d)] || per.ora, giorni, fine: per.a,
            oreUniche: new Set(Object.values(per.orePerGiorno)).size <= 1 });
          break;
        }
      }
    }
  }

  if (cand.length === 0) {
    // C'erano date ma sono tutte passate => evento finito.
    if (parsed.date.length || parsed.periodi.length) return null;
    // Campo "appuntamenti" non interpretabile: ripiego su data_inizio / data_fine.
    if (!fineFeat || fineFeat < OGGI) return null;
    const inizio = inizioFeat && inizioFeat <= fineFeat ? inizioFeat : fineFeat;
    if (inizio === fineFeat) return { tipo: 'date', iso: fineFeat, ora: null, altre: 0 };
    return { tipo: 'continuo', iso: inizio > OGGI ? inizio : OGGI, ora: null, da: inizio, a: fineFeat, durata: diffDays(inizio, fineFeat) + 1 };
  }

  // Meglio una data precisa o ricorrente che un periodo continuo.
  const preferiti = cand.filter((c) => c.tipo !== 'continuo');
  const pool = (preferiti.length ? preferiti : cand).sort(byIsoOra);
  const best = pool[0];
  const giorniDistinti = new Set(dateFuture.map((d) => d.iso));
  best.altre = best.tipo === 'date' ? Math.max(0, giorniDistinti.size - 1) : 0;
  return best;
}

function elencoOccorrenze(parsed) {
  // Tutte le date future (entro l'orizzonte) di un evento con date puntuali o ricorrenti.
  // Gli eventi "continui" (mostre, rassegne) non hanno date puntuali: restituiscono [] e valgono dataSort..dataFine.
  const limiteOrizzonte = addDays(OGGI, CONFIG.ORIZZONTE_OCCORRENZE_GG);
  const trovate = new Map(); // iso -> ora

  for (const d of parsed.date) {
    if (d.iso && d.iso >= OGGI && d.iso <= limiteOrizzonte && !trovate.has(d.iso)) trovate.set(d.iso, d.ora || '');
  }
  for (const per of parsed.periodi) {
    if (!per.da || !per.a || per.a < OGGI) continue;
    if (per.giorni.size === 0 || per.giorni.size >= 5) continue; // periodo continuo
    const ultimo = per.a < limiteOrizzonte ? per.a : limiteOrizzonte;
    for (let d = per.da > OGGI ? per.da : OGGI; d <= ultimo; d = addDays(d, 1)) {
      if (per.giorni.has(weekday(d)) && !trovate.has(d)) trovate.set(d, per.orePerGiorno[weekday(d)] || per.ora || '');
    }
  }
  return [...trovate.entries()]
    .sort((a, b) => (a[0] < b[0] ? -1 : 1))
    .slice(0, CONFIG.MAX_OCCORRENZE_PER_EVENTO)
    .map(([d, o]) => ({ d, o }));
}

function elencoGiorni(giorni) {
  const ordinati = [...giorni].sort((a, b) => (a || 7) - (b || 7)); // lunedì primo
  const nomi = ordinati.map((g) => NOMI_GIORNI[g]);
  return nomi.length > 1 ? `${nomi.slice(0, -1).join(', ')} e ${nomi[nomi.length - 1]}` : nomi[0];
}

function riepilogo(o) {
  // Se gli orari cambiano da un giorno all'altro, nel riepilogo di una serie non si indica un'ora sola.
  const ora = o.ora && !(o.tipo === 'ricorrente' && o.oreUniche === false) ? ` ore ${o.ora}` : '';
  switch (o.tipo) {
    case 'date':
      return o.altre > 0
        ? `Prossima data: ${fmt(o.iso)}${ora} (+${o.altre} altre date)`
        : `${fmt(o.iso)}${ora}`;
    case 'ricorrente':
      return `Ogni ${elencoGiorni(o.giorni)}${ora} fino al ${fmt(o.fine)}`;
    default:
      return `Dal ${fmt(o.da)} al ${fmt(o.a)}`;
  }
}

// ---------------------------------------------------------------- categoria

const REGOLE = [
  ['Rievocazione storica/Palio', /\b(rievocazion\w*|palio|corteo storico|calcio storico)\b/g],
  ['Sagra', /\bsagr\w*\b/g],
  ['Evento sportivo', /\b(maratona|corri|corsa|torneo|campionato|gara|trail|ciclismo|ciclistic\w*|pallavolo|basket|rugby|calcio|nuoto|tennis)\b/g],
  ['Fiera', /\b(fiera|fierucola|antiquari\w*|expo)\b/g],
  ['Mercato', /\b(mercato|mercatino|mercatini)\b/g],
  ['Mostra/Esposizione', /\b(mostra|mostre|esposizion\w*|museo|installazion\w*|galleria|fotografic\w*)\b/g],
  ['Concerto', /\b(concert\w*|musica|musicale|orchestra|jazz|recital|live|coro|quartet\w*|trio|arie|opera)\b/g],
  ['Spettacolo teatrale', /\b(teatr\w*|spettacol\w*|commedia|musical|monologo|narrazione|danza|performance)\b/g],
  ['Evento gastronomico', /\b(degustazion\w*|enogastronom\w*|vino|vini|cena|gastronom\w*|food|cibo|aperitivo)\b/g],
  ['Festival', /\b(festival|rassegna)\b/g],
  ['Festa paesana', /\b(festa|feste)\b/g],
  ['Serata/Discoteca', /\b(discoteca|dj set|dj|clubbing|serata danzante)\b/g],
];

function classifica(nome, descrizione) {
  const t = senzaAccenti(nome).toLowerCase();
  const d = senzaAccenti(descrizione).toLowerCase().slice(0, 300);
  let migliore = 'Altro';
  let punteggioMax = 0;
  for (const [cat, re] of REGOLE) {
    const punti = 3 * (t.match(re) || []).length + (d.match(re) || []).length;
    if (punti > punteggioMax) { punteggioMax = punti; migliore = cat; } // a parità vince la regola più in alto
  }
  return migliore;
}

// ---------------------------------------------------------------- utilità varie

const pulisci = (s) => String(s || '').replace(/\s+/g, ' ').trim();

function tronca(s, max) {
  const t = pulisci(s);
  if (t.length <= max) return t;
  const taglio = t.slice(0, max);
  return taglio.slice(0, Math.max(taglio.lastIndexOf(' '), max - 40)).replace(/[,.;:\s]+$/, '') + '…';
}

function primoUrl(s) {
  const m = /https?:\/\/\S+/.exec(s || '');
  if (!m) return null;
  try {
    const u = new URL(m[0]);
    [...u.searchParams.keys()]
      .filter((k) => /^(utm_|_gl|_ga|qub)/i.test(k))
      .forEach((k) => u.searchParams.delete(k));
    return u.toString();
  } catch { return null; }
}

function coordinate(geom) {
  if (!geom || !Array.isArray(geom.coordinates)) return null;
  const punti = geom.type === 'Point' ? [geom.coordinates] : geom.coordinates;
  const b = CONFIG.BBOX;
  for (const p of punti) {
    if (!Array.isArray(p)) continue;
    const lon = Number(p[0]);
    const lat = Number(p[1]);
    if (lat > b.latMin && lat < b.latMax && lon > b.lonMin && lon < b.lonMax) {
      return { lat: +lat.toFixed(6), lon: +lon.toFixed(6) };
    }
  }
  return null;
}

function hashCorto(s) {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
  return h.toString(36);
}

// ---------------------------------------------------------------- normalizzazione di un evento

function normalizza(f) {
  const p = f.properties || {};
  const nome = pulisci(p.titolo);
  if (!nome) return { scarta: 'senza-titolo' };

  const parsed = parseAppuntamenti(p.appuntamenti);
  const occ = calcolaOccorrenza(parsed, parseIT(p.data_inizio), parseIT(p.data_fine));
  if (!occ) return { scarta: 'finito-o-senza-date' };
  if (occ.tipo === 'continuo' && occ.durata > CONFIG.MAX_DURATA_CONTINUI_GG) return { scarta: 'continuo-lungo' };

  const comune = String(p.comuni || '').split(/\r?\n/).map(pulisci).filter(Boolean)[0] || 'Firenze';
  const descrizione = tronca(p.descrizione, CONFIG.MAX_DESCRIZIONE);

  let c = coordinate(f.geometry);
  let approssimate = false;
  if (!c && comune.toLowerCase() === 'firenze') { c = { ...CONFIG.CENTRO_FIRENZE }; approssimate = true; }

  const luogo = parsed.luoghi[0] || '';
  const indirizzo = luogo ? `${luogo}, ${comune}` : comune;
  const mapsUrl = c && !approssimate
    ? `https://www.google.com/maps/search/?api=1&query=${c.lat},${c.lon}`
    : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(indirizzo)}`;

  const urlFF = pulisci(p.feelflorence_link) || null;
  const slug = urlFF ? urlFF.replace(/\/+$/, '').split('/').pop().slice(0, 80) : hashCorto(nome);

  const continuo = occ.tipo === 'continuo';
  let occorrenze = continuo ? [] : elencoOccorrenze(parsed);
  if (!continuo && occorrenze.length === 0) occorrenze = [{ d: occ.iso, o: occ.ora || '' }];
  return {
    id: `fi-${slug}`,
    nome,
    locandina: '',
    categoria: classifica(nome, p.descrizione),
    descrizione,
    provincia: 'Firenze',
    comune,
    indirizzo,
    piuLuoghi: Math.max(0, parsed.luoghi.length - 1),
    riepilogoDate: riepilogo(occ),
    orario: occ.ora || '',
    dataSort: fmtSort(continuo ? occ.da : occ.iso),
    dataFine: fmtSort(continuo ? occ.a : occ.iso),
    occorrenze: occorrenze.map((x) => ({ d: fmtSort(x.d), o: x.o })),
    mapsUrl,
    lat: c ? c.lat : null,
    lon: c ? c.lon : null,
    coordApprossimate: approssimate,
    instagram: '',
    inEvidenza: false,
    fonte: 'firenze',
    url: urlFF,
    sitoUfficiale: primoUrl(p.link),
  };
}

// ---------------------------------------------------------------- main

let geo;
try {
  geo = JSON.parse(fs.readFileSync(inPath, 'utf8'));
} catch (e) {
  console.error('GeoJSON non leggibile:', e.message);
  process.exit(1);
}

const feats = Array.isArray(geo.features) ? geo.features : [];
if (feats.length === 0) {
  console.error('Il GeoJSON non contiene eventi: non sovrascrivo il file esistente.');
  process.exit(1);
}

const eventi = [];
const scartati = {};
const visti = new Set();
const conta = (k) => { scartati[k] = (scartati[k] || 0) + 1; };

for (const f of feats) {
  try {
    const ev = normalizza(f);
    if (ev.scarta) { conta(ev.scarta); continue; }
    if (visti.has(ev.id)) { conta('id-duplicato'); continue; }
    visti.add(ev.id);
    eventi.push(ev);
  } catch (e) {
    conta('errore-parsing');
    console.warn(`Evento ignorato (id ${f && f.id}): ${e.message}`);
  }
}

if (eventi.length === 0) {
  console.error('Nessun evento valido dopo la normalizzazione: non sovrascrivo il file esistente.');
  process.exit(1);
}

if (fs.existsSync(outPath)) {
  try {
    const prec = JSON.parse(fs.readFileSync(outPath, 'utf8'));
    const nPrec = Array.isArray(prec.eventi) ? prec.eventi.length : 0;
    if (nPrec >= 20 && eventi.length < nPrec * CONFIG.SOGLIA_CALO_ANOMALO) {
      console.error(`Calo anomalo: ${nPrec} -> ${eventi.length} eventi. Controlla il formato del dataset.`);
      process.exit(1);
    }
  } catch { /* file precedente illeggibile: si procede */ }
}

eventi.sort((a, b) => (a.dataSort < b.dataSort ? -1 : a.dataSort > b.dataSort ? 1 : a.nome.localeCompare(b.nome, 'it')));

fs.writeFileSync(
  outPath,
  JSON.stringify({
    aggiornato: new Date().toISOString(),
    fonte: CONFIG.FONTE,
    licenza: CONFIG.LICENZA,
    licenzaUrl: CONFIG.LICENZA_URL,
    attribuzione: CONFIG.ATTRIBUZIONE,
    totale: eventi.length,
    eventi,
  })
);

const perCategoria = {};
eventi.forEach((e) => { perCategoria[e.categoria] = (perCategoria[e.categoria] || 0) + 1; });
console.log(`Oggi (Roma): ${OGGI}`);
console.log(`Firenze: ${feats.length} schede in ingresso -> ${eventi.length} eventi`);
console.log('Scartati:', JSON.stringify(scartati));
console.log('Per categoria:', JSON.stringify(perCategoria));
console.log(`Senza coordinate: ${eventi.filter((e) => e.lat === null).length}, approssimate: ${eventi.filter((e) => e.coordApprossimate).length}`);
