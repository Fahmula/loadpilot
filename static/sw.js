// Minimal service worker so LoadPilot can be installed as an app.
// It deliberately caches nothing: pages contain live load and banking data
// that should always come from the server.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
