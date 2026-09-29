// Minimaler Service-Worker: macht Jarvis als App installierbar und cached die Oberfläche.
const CACHE = "jarvis-v1";
const ASSETS = ["/", "/static/style.css", "/static/app.js", "/static/icon.svg", "/static/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api/") || e.request.method !== "GET") return; // API nie cachen
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
