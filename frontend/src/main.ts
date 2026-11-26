import "./style.css";
import { mountAlerts } from "./alerts";
import { mountDiag } from "./diag";
import { mountLearn } from "./learn";
import { mountMap } from "./map";
import { mountNight } from "./night";
import { loadSettings } from "./settings";
import { mountSettings } from "./settingsPage";
import { mockStream } from "./mock";
import { mountTrips } from "./trips";
import { mountTruck } from "./truck";
import type { Snapshot } from "./types";
import { mountViews } from "./views";

const conn = document.getElementById("conn")!;
const debug = document.getElementById("debug")!;
document.getElementById("debug-toggle")!.onclick = () => { debug.hidden = !debug.hidden; };

const toastEl = document.getElementById("toast")!;
let toastTimer: number | undefined;
function toast(text: string) {
  toastEl.textContent = text;
  toastEl.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { toastEl.hidden = true; }, 1800);
}
const updateViews = mountViews(document.getElementById("app")!, document.getElementById("left")!, toast);
const updateMapP = loadSettings().then(() =>
  mountMap(document.getElementById("map")!, document.getElementById("map-note")!, document.getElementById("map-toggle") as HTMLButtonElement));
const trips = mountTrips(document.getElementById("trips")!);
const diag = mountDiag(document.getElementById("diag")!);
const truckPage = document.getElementById("truck")!;
truckPage.innerHTML = `<div class="truck-big"></div><div class="truck-side"></div>`;
const updateTruckBig = mountTruck(truckPage.querySelector(".truck-big")!, false);
const updateTruckList = mountTruck(truckPage.querySelector(".truck-side")!, true);
truckPage.querySelector(".truck-side svg")?.remove(); // the side panel only needs the list
const updateTruckMini = mountTruck(document.getElementById("truck-mini")!, false);
const updateAlerts = mountAlerts(document.getElementById("alerts")!);
const updateNight = mountNight(document.getElementById("dimmer")!);
mountSettings(document.getElementById("settings")!);
const learn = mountLearn(document.getElementById("learn")!);
loadSettings();
let lastGps = 0;

// Views: #dash (default), #truck, #trips, #diag, #settings. The dash keeps updating while hidden so the map is current on return.
const views: Record<string, { el: HTMLElement; onShow?: () => void }> = {
  dash: { el: document.getElementById("app")! },
  trips: { el: document.getElementById("trips")!, onShow: trips.show },
  truck: { el: truckPage },
  diag: { el: document.getElementById("diag")!, onShow: diag.show },
  settings: { el: document.getElementById("settings")!, onShow: loadSettings },
  learn: { el: document.getElementById("learn")!, onShow: learn.show },
};
function route() {
  const name = location.hash.slice(1) in views ? location.hash.slice(1) : "dash";
  for (const [k, v] of Object.entries(views)) v.el.hidden = k !== name;
  document.querySelectorAll<HTMLAnchorElement>("#nav a").forEach((a) => a.classList.toggle("on", a.hash === `#${name}`));
  views[name].onShow?.();
  if (name === "dash") updateMapP.then(() => window.dispatchEvent(new Event("resize")));
}
window.addEventListener("hashchange", route);
route();

async function onSnap(s: Snapshot) {
  updateViews(s);
  trips.update(s);
  diag.update(s);
  updateAlerts(s);
  updateTruckMini(s);
  if (!truckPage.hidden) { updateTruckBig(s); updateTruckList(s); }
  updateNight(s.gps);
  if (!debug.hidden) debug.textContent = JSON.stringify(s, null, 1);
  const now = performance.now();
  if (now - lastGps > 200) { lastGps = now; (await updateMapP)(s.gps, s.nav); }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { conn.textContent = "live"; conn.className = "ok"; };
  ws.onmessage = (e) => onSnap(JSON.parse(e.data));
  ws.onclose = () => { conn.textContent = "reconnecting"; conn.className = ""; setTimeout(connect, 1000); };
}

if (new URLSearchParams(location.search).has("mock")) { conn.textContent = "mock data"; mockStream(onSnap); }
else connect();
