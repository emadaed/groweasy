// Service Worker DISABLED
// This file exists only to unregister any previously cached service workers

self.addEventListener('install', function(event) {
    self.skipWaiting();
});

self.addEventListener('activate', function(event) {
    // Unregister this service worker
    self.registration.unregister();

    // Clear all caches
    event.waitUntil(
        caches.keys().then(function(cacheNames) {
            return Promise.all(
                cacheNames.map(function(cacheName) {
                    return caches.delete(cacheName);
                })
            );
        })
    );
});
