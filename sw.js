// ===================== SERVICE WORKER — Stase Toscana =====================
// Scopo: (1) rendere il sito installabile come app (requisito tecnico dei
// browser: un service worker registrato + un manifest), (2) far funzionare
// almeno la shell del sito quando manca la connessione.
//
// Strategia: "network-first" per tutto — prova sempre la rete per avere dati
// aggiornati (eventi cambiano di continuo), e solo se la rete non risponde
// usa la copia in cache. Le richieste verso altri domini (tile della mappa,
// JSON eventi serviti da GitHub Pages con altro dominio, ecc.) non vengono
// mai intercettate: passano dritte alla rete come farebbero normalmente.
//
// Cambia CACHE_NAME quando aggiorni questo file per invalidare la cache
// precedente sui dispositivi che hanno già installato l'app.
// ============================================================================

const CACHE_NAME = 'stase-toscana-v1';
const APP_SHELL = [
  '/',
  '/index.html',
  '/logo-stase.png',
  '/manifest.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return; // solo il nostro dominio

  event.respondWith(
    fetch(request)
      .then((response) => {
        const copia = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(request, copia));
        return response;
      })
      .catch(() =>
        caches.match(request).then((cached) => cached || caches.match('/index.html'))
      )
  );
});
