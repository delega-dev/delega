// Task Service Worker with Push Notifications
const CACHE_NAME = 'cache-v7';
const urlsToCache = [
  '/',
  '/manifest.json'
];

// ============ Install ============
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(urlsToCache))
      .then(() => self.skipWaiting())  // Activate immediately
  );
});

// ============ Activate ============
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames
          .filter(name => name !== CACHE_NAME)
          .map(name => caches.delete(name))
      );
    }).then(() => self.clients.claim())  // Take control of pages
  );
});

// ============ Fetch ============
self.addEventListener('fetch', event => {
  // Network first, fallback to cache
  event.respondWith(
    fetch(event.request)
      .catch(() => caches.match(event.request))
  );
});

// ============ Push Notification Received ============
self.addEventListener('push', event => {
  console.log('[SW] Push received:', event);
  
  let payload = {
    title: 'Delega',
    body: 'You have a notification',
    icon: '/assets/icon-192.png',
    badge: '/assets/badge-72.png',
    data: {}
  };
  
  // Parse payload if present
  if (event.data) {
    try {
      const data = event.data.json();
      payload = { ...payload, ...data };
    } catch (e) {
      // If not JSON, use as body text
      payload.body = event.data.text();
    }
  }
  
  // Build notification options
  const options = {
    body: payload.body,
    icon: payload.icon,
    badge: payload.badge,
    tag: payload.tag || 'delega-notification',
    data: payload.data,
    requireInteraction: payload.requireInteraction || false,
    silent: payload.silent || false,
  };
  
  event.waitUntil(
    self.registration.showNotification(payload.title, options)
  );
});

// ============ Notification Click ============
self.addEventListener('notificationclick', event => {
  console.log('[SW] Notification clicked:', event);
  
  event.notification.close();
  
  // Get URL to open (from notification data or default to root)
  const urlToOpen = event.notification.data?.url || '/';
  
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then(windowClients => {
        // Check if app is already open
        for (const client of windowClients) {
          if (client.url.includes(self.location.origin)) {
            // Focus existing window and navigate
            client.focus();
            if (urlToOpen !== '/') {
              client.navigate(urlToOpen);
            }
            return;
          }
        }
        // Open new window
        return clients.openWindow(urlToOpen);
      })
  );
});

// ============ Push Subscription Change ============
self.addEventListener('pushsubscriptionchange', event => {
  console.log('[SW] Push subscription changed');
  
  // Re-subscribe with new subscription
  event.waitUntil(
    self.registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: event.oldSubscription?.options?.applicationServerKey
    }).then(subscription => {
      // Send new subscription to backend
      return fetch('/api/push/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          endpoint: subscription.endpoint,
          keys: {
            p256dh: arrayBufferToBase64(subscription.getKey('p256dh')),
            auth: arrayBufferToBase64(subscription.getKey('auth'))
          }
        })
      });
    })
  );
});

// Helper for pushsubscriptionchange
function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}
