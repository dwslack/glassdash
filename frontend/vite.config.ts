import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, type ProxyOptions } from "vite";

// In dev, Vite serves the UI with hot reload and forwards data calls to dashd on :8000.
// dashd uses no cookies, so they're dropped: big cookies from other apps on 127.0.0.1/localhost
// otherwise trip "431 Request Header Fields Too Large".
// With a certificate in data/tls (just https-cert), both Vite and dashd use HTTPS, which the
// iPad needs before it lets the page use the microphone.
const tls = fileURLToPath(new URL("../data/tls/", import.meta.url));
const https = existsSync(tls + "cert.pem") && existsSync(tls + "key.pem")
  ? { cert: readFileSync(tls + "cert.pem"), key: readFileSync(tls + "key.pem") }
  : undefined;
const backend = https ? "https://localhost:8000" : "http://localhost:8000";

const toDashd = (target: string, ws = false): ProxyOptions => ({
  target,
  ws,
  secure: false, // dashd's cert is for the LAN name; this hop never leaves the machine
  configure: (proxy) => {
    proxy.on("proxyReq", (req) => req.removeHeader("cookie"));
    proxy.on("proxyReqWs", (req) => req.removeHeader("cookie"));
  },
});

export default defineConfig({
  server: {
    https,
    proxy: {
      "/api": toDashd(backend),
      "/tiles": toDashd(backend),
      "/map-assets": toDashd(backend),
      "/ws": toDashd(backend.replace("http", "ws"), true),
    },
  },
});
