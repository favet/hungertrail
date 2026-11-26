// Forward beta.hungertrail.com to the Render app and tell it to serve the beta.
// One app, two games: no second Render service, so no extra free hours and no cold start.
const ORIGIN = "hungertrail.onrender.com";

export default {
  async fetch(request) {
    const url = new URL(request.url);
    url.protocol = "https:";
    url.hostname = ORIGIN;
    url.port = "";
    const headers = new Headers(request.headers);
    headers.delete("host");
    headers.set("x-ht-site", "beta");
    return fetch(url, { method: request.method, headers, body: request.body, redirect: "manual" });
  },
};
