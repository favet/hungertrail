const TARGET = "https://hungertrail.onrender.com/healthz";

export default {
  async scheduled(_event, _env, ctx) {
    ctx.waitUntil(fetch(TARGET, { headers: { "user-agent": "hungertrail-keepalive" } }));
  },
};
