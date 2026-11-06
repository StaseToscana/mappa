#!/usr/bin/env node
/**
 * immagine-tema.js
 *
 * Piano B per gli eventi che dopo foto-luoghi.js sono ancora senza immagine:
 * sceglie un'illustrazione TUA, per tema, in base alle parole del titolo
 * (es. "Sagra del Cinghiale" -> cinghiale, "Mostra del tartufo" -> tartufo).
 *
 * Uso:  node scripts/immagine-tema.js eventi_sagre.json immagini-sagre
 *
 * - Le illustrazioni sono file che metti tu nella cartella (cinghiale.jpg, castagne.png, ...).
 *   Se il file di un tema non esiste, quel tema viene saltato: puoi aggiungerle un po' alla volta.
 * - Non tocca mai un evento che ha gia' una locandina.
 * - Marca l'evento con immagineIllustrativa: true, cosi' il sito puo' scrivere
 *   "Immagine illustrativa" ed evitare che sembri una foto del vero evento.
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
const TEMA_GENERICO = 'sagra'; // per qualunque "Sagra" senza un tema piu' preciso

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
  const st = { conTema: 0, senzaFile: new Set(), saltati: 0 };
  for (const ev of dati.eventi || []) {
    if (ev.locandina) continue;
    let tema = trovaTema(ev.nome);
    if (!tema && ev.categoria === 'Sagra') tema = TEMA_GENERICO;
    if (!tema) { st.saltati++; continue; }
    let file = fileDelTema(cartella, tema);
    if (!file && ev.categoria === 'Sagra') file = fileDelTema(cartella, TEMA_GENERICO); // ripiego sul generico
    if (!file) { st.senzaFile.add(tema); continue; }
    ev.locandina = `${BASE_URL}/${NOME_CARTELLA_URL}/${file}`;
    ev.immagineIllustrativa = true;
    st.conTema++;
  }
  return st;
}

if (require.main === module) {
  try {
    const [, , eventiPath, cartella] = process.argv;
    if (!eventiPath || !cartella) { console.error('Uso: node immagine-tema.js <eventi.json> <cartella-immagini>'); process.exit(2); }
    const dati = JSON.parse(fs.readFileSync(eventiPath, 'utf8'));
    const st = processa(dati, cartella);
    if (st.conTema > 0) fs.writeFileSync(eventiPath, JSON.stringify(dati));
    console.log(`Immagini illustrative assegnate: ${st.conTema} | eventi senza tema: ${st.saltati}`);
    if (st.senzaFile.size) console.log('Temi senza file in ' + cartella + '/: ' + [...st.senzaFile].join(', '));
  } catch (e) {
    console.error('Errore imprevisto (gli eventi restano validi):', e.message);
    process.exit(0);
  }
}

module.exports = { trovaTema, processa };
