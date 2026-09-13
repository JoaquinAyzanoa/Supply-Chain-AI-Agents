/* The Control Tower's service worker: makes the app installable and shows the director's
   push notifications for approvals. It caches nothing: every request goes to the network,
   so what a person sees is always the live desk. */

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// No fetch handler on purpose: nothing is cached or intercepted, every request
// reaches the director as if the worker were not there.

self.addEventListener("push", (event) => {
  let data = { title: "Control Tower", body: "", url: "/approvals", tag: "approval" };
  try {
    data = { ...data, ...event.data.json() };
  } catch {
    // an empty or odd payload: still wake the person up
  }
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      tag: data.tag,
      icon: "/icon.svg",
      badge: "/icon.svg",
      data: { url: data.url },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/approvals";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    }),
  );
});
