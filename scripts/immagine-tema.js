#!/usr/bin/env node
/**
 * immagine-tema.js
 *
 * Per gli eventi che dopo foto-luoghi.js sono ancora senza immagine sceglie un'illustrazione
 * TUA, in due passi:
 *   1) tema specifico dalle parole del titolo (es. "Sagra del Cinghiale" -> cinghiale.jpg)
 *   2) altrimenti l'illustrazione della CATEGORIA dell'evento (es. Spettacolo teatrale -> teatro.svg)
 *
 * Uso:  node scripts/immagine-tema.js eventi_sagre.json immagini-sagre
 *
 * - Le immagini sono file nella cartella (cinghiale.jpg, teatro.svg, ...). Se il file non esiste
 *   quel passo viene saltato: puoi sostituire o aggiungere immagini un po' alla volta.
 *   A parita' di nome vince il formato nell'ordine jpg, jpeg, png, webp, svg.
 * - Non tocca mai un evento che ha gia' una locandina.
 * - Marca l'evento con immagineIllustrativa: true, cosi' il sito scrive "Immagine illustrativa"
 *   ed evita che sembri una foto del vero evento.
 * - Nessuna dipendenza esterna (Node 18+).
 */
'use strict';

const fs = require('fs');
const path = require('path');

const BASE_URL = 'https://stasetoscana.it';
const NOME_CARTELLA_URL = 'immagini-sagre';
const ESTENSIONI = ['jpg', 'jpeg', 'png', 'webp', 'svg'];

// ordine = priorita' (il primo tema che combacia vince)
const TEMI = [
  ['cinghiale', /cinghial/],
  ['tartufo', /tartuf/],
  ['castagne', /castagn|marron|frugiolo|necci/],
  ['funghi', /fungh|porcin/],
  ['olio', /\bolio|frantoi|oliv/],
  ['vino', /\bvin[oi]\b|cantine|vendemmi|\buva\b|degusta|enoteca/],
  ['pesce', /pesce|fritto misto|totan|baccal|acciugh|cacciucc|gamber|calamar/],
  ['carne', /carne|brace|bistecc|chianina|salsicc|tortell|papero|cunigliol|coniglio|arrost|porchett|grigliat/],
  ['dolci', /schiacci|dolci|cantucc|biscott|cioccolat|castagnacc|torta|gelato/],
  ['pane', /\bpane\b|pizza|focacc|crostini|fettunta/],
];
// Illustrazione di ripiego per categoria (nome file senza estensione)
const FILE_CATEGORIA = {
  'Sagra': 'sagra',
  'Evento gastronomico': 'gastronomico',
  'Evento sportivo': 'sport',
  'Festa paesana': 'festa',
  'Festival': 'festival',
  'Fiera': 'fiera',
  'Mercato': 'mercato',
  'Mostra/Esposizione': 'mostra',
  'Rievocazione storica/Palio': 'rievocazione',
  'Spettacolo teatrale': 'teatro',
  'Serata/Discoteca': 'serata',
  'Concerto': 'concerto',
  'Altro': 'altro',
};

const senzaAccenti = (s) => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '');

function trovaTema(titolo) {
  const t = senzaAccenti(titolo).toLowerCase();
  for (const [tema, re] of TEMI) if (re.test(t)) return tema;
  return null;
}

function fileDelTema(cartella, tema) {
  for (const ext of ESTENSIONI) {
    const nome = `${tema}.${ext}`;
    if (fs.existsSync(path.join(cartella, nome))) return nome;
  }
  return null;
}

function processa(dati, cartella, log = console.log) {
  const st = { conTema: 0, perCategoria: 0, senzaFile: new Set(), saltati: 0 };
  for (const ev of dati.eventi || []) {
    if (ev.locandina) continue;
    let file = null, perCategoria = false;
    const tema = trovaTema(ev.nome);
    if (tema) {
      file = fileDelTema(cartella, tema);
      if (!file) st.senzaFile.add(tema);
    }
    if (!file && FILE_CATEGORIA[ev.categoria]) {
      file = fileDelTema(cartella, FILE_CATEGORIA[ev.categoria]);
      perCategoria = !!file;
    }
    if (!file) { st.saltati++; continue; }
    ev.locandina = `${BASE_URL}/${NOME_CARTELLA_URL}/${file}`;
    ev.immagineIllustrativa = true;
    if (perCategoria) st.perCategoria++; else st.conTema++;
  }
  return st;
}

if (require.main === module) {
  try {
    const [, , eventiPath, cartella] = process.argv;
    if (!eventiPath || !cartella) { console.error('Uso: node immagine-tema.js <eventi.json> <cartella-immagini>'); process.exit(2); }
    const dati = JSON.parse(fs.readFileSync(eventiPath, 'utf8'));
    const st = processa(dati, cartella);
    if (st.conTema + st.perCategoria > 0) fs.writeFileSync(eventiPath, JSON.stringify(dati));
    console.log(`Immagini illustrative assegnate: ${st.conTema + st.perCategoria} (per tema: ${st.conTema}, per categoria: ${st.perCategoria}) | eventi ancora senza immagine: ${st.saltati}`);
    if (st.senzaFile.size) console.log('Temi specifici senza file in ' + cartella + '/ (facoltativi): ' + [...st.senzaFile].join(', '));
  } catch (e) {
    console.error('Errore imprevisto (gli eventi restano validi):', e.message);
    process.exit(0);
  }
}

module.exports = { trovaTema, processa };
