import type { Body, Snapshot } from "./types";

// Top-down 2011 Silverado crew cab, front at the top. Doors swing out on their front hinges.
const DOORS = [
  { key: "door_driver", x: 14, y: 78, side: -1 },
  { key: "door_rear_left", x: 14, y: 108, side: -1 },
  { key: "door_passenger", x: 106, y: 78, side: 1 },
  { key: "door_rear_right", x: 106, y: 108, side: 1 },
] as const;

let instances = 0;

function svg(): string {
  const n = ++instances; // gradient ids must be unique per page

  const doors = DOORS.map((d) =>
    `<g class="door" data-k="${d.key}" style="transform-origin:${d.x}px ${d.y}px" data-side="${d.side}">
       <rect x="${d.side < 0 ? d.x - 2 : d.x - 2}" y="${d.y}" width="4" height="28" rx="1.5"/></g>`).join("");
  return `<svg viewBox="-10 -70 140 350" class="truck-svg">
    <defs>
      <linearGradient id="beam${n}" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#ffe9a8" stop-opacity=".55"/><stop offset="1" stop-color="#ffe9a8" stop-opacity="0"/></linearGradient>
      <linearGradient id="hibeam${n}" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#bfe0ff" stop-opacity=".6"/><stop offset="1" stop-color="#bfe0ff" stop-opacity="0"/></linearGradient>
    </defs>
    <path class="beam low" d="M28 14 L6 -40 L54 -40 L44 14 Z M92 14 L114 -40 L66 -40 L76 14 Z" fill="url(#beam${n})"/>
    <path class="beam high" d="M28 14 L-8 -68 L58 -68 L44 14 Z M92 14 L128 -68 L62 -68 L76 14 Z" fill="url(#hibeam${n})"/>
    <rect class="shell" x="16" y="14" width="88" height="232" rx="14"/>
    <rect class="hood" x="22" y="18" width="76" height="46" rx="8"/>
    <path class="glass" d="M24 68 Q60 60 96 68 L92 80 Q60 74 28 80 Z"/>
    <rect class="roof" x="26" y="82" width="68" height="52" rx="5"/>
    <path class="glass" d="M28 136 Q60 140 92 136 L92 142 L28 142 Z"/>
    <rect class="bed" x="24" y="148" width="72" height="88" rx="2"/>
    <g class="tailgate"><rect x="22" y="238" width="76" height="6" rx="2"/></g>
    <rect class="tailgate-open" x="22" y="244" width="76" height="22" rx="2"/>
    ${doors}
    <ellipse class="lamp head" cx="30" cy="16" rx="9" ry="3.5"/><ellipse class="lamp head" cx="90" cy="16" rx="9" ry="3.5"/>
    <rect class="lamp turn left" x="16" y="18" width="7" height="6" rx="2"/><rect class="lamp turn right" x="97" y="18" width="7" height="6" rx="2"/>
    <rect class="lamp tail" x="18" y="236" width="8" height="10" rx="2"/><rect class="lamp tail" x="94" y="236" width="8" height="10" rx="2"/>
    <rect class="lamp turn left rear" x="18" y="228" width="8" height="7" rx="2"/><rect class="lamp turn right rear" x="94" y="228" width="8" height="7" rx="2"/>
    <rect class="lamp chmsl" x="50" y="135" width="20" height="3" rx="1"/>
    <rect class="lamp reverse" x="30" y="246" width="8" height="3"/><rect class="lamp reverse" x="82" y="246" width="8" height="3"/>
    <g class="icon lock" transform="translate(52 98)"><rect x="0" y="7" width="16" height="12" rx="2"/><path d="M3 7 V4 a5 5 0 0 1 10 0 V7" fill="none" stroke-width="2.2"/></g>
    <g class="icon belt" transform="translate(30 88)"><circle cx="6" cy="3" r="3"/><path d="M1 18 L3 8 H9 L11 18 Z"/><path class="strap" d="M0 7 L12 17" stroke-width="2.2"/></g>
    <g class="icon pbrake" transform="translate(52 182)"><circle cx="8" cy="8" r="9" fill="none" stroke-width="2"/><text x="8" y="12.5" text-anchor="middle">P</text></g>
  </svg>`;
}

const LABELS: [keyof Body, string][] = [
  ["door_driver", "Driver door"], ["door_passenger", "Passenger door"], ["door_rear_left", "Rear left door"],
  ["door_rear_right", "Rear right door"], ["tailgate", "Tailgate"], ["hood", "Hood"], ["locked", "Locked"],
  ["low_beam", "Headlights"], ["high_beam", "High beams"], ["fog", "Fog lights"], ["turn_left", "Left signal"],
  ["turn_right", "Right signal"], ["brake", "Brake lights"], ["reverse", "Reverse"], ["belt_driver", "Driver belt"],
  ["parking_brake", "Parking brake"],
];

export function mountTruck(el: HTMLElement, withList: boolean): (s: Snapshot) => void {
  el.innerHTML = svg() + (withList ? `<div class="truck-list"></div><p class="note truck-note"></p>` : "");
  const root = el.querySelector("svg")!;
  const list = el.querySelector<HTMLElement>(".truck-list");
  const note = el.querySelector<HTMLElement>(".truck-note");
  let last = "";
  return (s) => {
    const b = s.body;
    el.classList.toggle("nodata", !b);
    const key = JSON.stringify(b) + String(s.trip?.running);
    if (key === last) return;
    last = key;
    const on = (k: keyof Body) => Boolean(b?.[k]);
    for (const d of DOORS) {
      const g = root.querySelector<SVGGElement>(`[data-k="${d.key}"]`)!;
      g.classList.toggle("open", on(d.key));
      g.style.transform = on(d.key) ? `rotate(${-d.side * 55}deg)` : "";
    }
    root.classList.toggle("tg-open", on("tailgate"));
    root.classList.toggle("hood-open", on("hood"));
    root.classList.toggle("low", on("low_beam") || on("parking_lights"));
    root.classList.toggle("beams", on("low_beam"));
    root.classList.toggle("high", on("high_beam"));
    root.classList.toggle("turn-l", on("turn_left"));
    root.classList.toggle("turn-r", on("turn_right"));
    root.classList.toggle("braking", on("brake"));
    root.classList.toggle("rev", on("reverse"));
    root.classList.toggle("locked", on("locked"));
    root.classList.toggle("unbelted", Boolean(b && s.trip?.running && !on("belt_driver"))); // only nag with the engine running
    root.classList.toggle("pbrake-on", on("parking_brake"));
    if (list) {
      list.innerHTML = b ? LABELS.filter(([k]) => k in b).map(([k, label]) => {
        const v = on(k);
        const text = k.startsWith("door") || k === "tailgate" || k === "hood" ? (v ? "OPEN" : "closed")
          : k === "locked" ? (v ? "locked" : "unlocked") : k === "belt_driver" ? (v ? "buckled" : "unbuckled") : (v ? "on" : "off");
        const alert = (k.startsWith("door") || k === "tailgate" || k === "hood") && v;
        return `<div class="${alert ? "hot" : v ? "lit" : ""}"><span>${label}</span><b>${text}</b></div>`;
      }).join("") : "";
      note!.textContent = !b ? "No body-bus data. On the truck this needs channel 2 of the CAN HAT on OBD pin 1 (see README)."
        : b.confirmed ? "" : "Sim demo: the body-bus frame IDs are placeholders until they're mapped on the truck.";
    }
  };
}
