// PDF 工具的离线引擎：只接管 /pdf-assets/ 的 GET 请求，先查 Cache Storage（由页面下载时写入），
// 没有再走网络。站点其他请求（页面、接口、实时进度）一概不碰，坏了也不会影响翻译功能。
// 需要停用时，发布一个在 activate 里调用 self.registration.unregister() 的版本即可。
const CACHE_NAME = 'bdw-pdf-assets';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET' || req.headers.has('range')) return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || !url.pathname.startsWith('/pdf-assets/')) return;
  event.respondWith(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.match(url.pathname))
      .then((hit) => hit || fetch(req))
      .catch(() => fetch(req)),
  );
});
