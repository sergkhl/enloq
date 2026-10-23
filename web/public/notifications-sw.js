// Notification-only worker: no fetch interception or offline correspondence cache.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) =>
  event.waitUntil(self.clients.claim()),
);

async function once(identity, expires) {
  const db = await new Promise((resolve, reject) => {
    const request = indexedDB.open("reply-notification-ids", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("seen");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction("seen", "readwrite"),
        store = tx.objectStore("seen");
      let fresh = false;
      const check = store.get(identity);
      check.onsuccess = () => {
        if (check.result === undefined) {
          fresh = true;
          store.put(expires + 86400, identity);
        }
      };
      const cursor = store.openCursor();
      cursor.onsuccess = () => {
        const item = cursor.result;
        if (!item) return;
        if (item.value < Date.now() / 1000) item.delete();
        item.continue();
      };
      tx.oncomplete = () => resolve(fresh);
      tx.onerror = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

self.addEventListener("push", (event) => {
  event.waitUntil(
    (async () => {
      const envelope = event.data?.json();
      if (
        !envelope ||
        !/^[a-f0-9]{32}$/.test(envelope.id) ||
        envelope.expires <= Date.now() / 1000
      )
        return;
      // The HttpOnly trusted-browser cookie authorizes this revalidation. No
      // subscription capabilities, messages or auth tokens go in worker storage.
      const response = await fetch("/api/notifications/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: envelope.id }),
      });
      if (!response.ok) return;
      const value = await response.json();
      if (!(await once(value.id, value.expires))) return;
      await self.registration.showNotification(
        `${value.sender} · ${value.account}`,
        {
          body: `${value.count > 1 ? `${value.count} messages · ` : ""}${value.text}`,
          tag: value.id,
          renotify: false,
          silent: value.silent,
          data: { id: value.id },
        },
      );
    })(),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    (async () => {
      const id = event.notification.data?.id;
      if (!/^[a-f0-9]{32}$/.test(id || "")) return;
      const windows = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      const existing = windows.find(
        (client) => new URL(client.url).origin === self.location.origin,
      );
      if (existing) {
        await existing.focus();
        existing.postMessage({ type: "notification-open", id });
      } else
        await self.clients.openWindow(
          `/?notification=${encodeURIComponent(id)}`,
        );
    })(),
  );
});
