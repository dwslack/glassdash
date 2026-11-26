import { saveSettings, settings } from "./settings";

// Voice button: the iPad's speech recognition turns speech into text, dashd answers
// (common commands locally, the rest via Claude), and the reply is spoken back.
// Speech recognition needs a secure page (HTTPS, or localhost) and, on the iPad, usually a connection.

type Rec = {
  lang: string; interimResults: boolean; continuous: boolean;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null; onend: (() => void) | null;
  start(): void; stop(): void; abort(): void;
};

const PAGE: Record<string, string> = {
  show_dash: "#dash", show_trips: "#trips", show_truck: "#truck", show_diagnostics: "#diag", show_settings: "#settings",
};

export function runAction(a: string): void {
  if (a in PAGE) location.hash = PAGE[a];
  else if (a.startsWith("view_")) { location.hash = "#dash"; window.dispatchEvent(new CustomEvent("glassdash:view", { detail: a.slice(5) })); }
  else if (a === "recenter" || a === "zoom_in" || a === "zoom_out") window.dispatchEvent(new CustomEvent("glassdash:map", { detail: a }));
  else if (a === "satellite_map") saveSettings({ basemap: "satellite" });
  else if (a === "street_map") saveSettings({ basemap: "street" });
}

export function mountVoice(wrap: HTMLElement): void {
  wrap.insertAdjacentHTML("beforeend", `
    <button id="voice" title="Talk to the dash" aria-label="Talk to the dash">
      <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
        <rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>
    </button>
    <div id="voice-bubble" hidden><div class="heard"></div><div class="reply"></div>
      <form class="type"><input placeholder="or type a question" autocomplete="off"><button>Ask</button></form></div>`);
  const button = wrap.querySelector<HTMLButtonElement>("#voice")!;
  const bubble = wrap.querySelector<HTMLElement>("#voice-bubble")!;
  const heard = bubble.querySelector<HTMLElement>(".heard")!;
  const replyEl = bubble.querySelector<HTMLElement>(".reply")!;
  const form = bubble.querySelector<HTMLFormElement>("form")!;
  const typed = form.querySelector<HTMLInputElement>("input")!;
  const SR = (window as unknown as { SpeechRecognition?: new () => Rec; webkitSpeechRecognition?: new () => Rec })
    .SpeechRecognition ?? (window as unknown as { webkitSpeechRecognition?: new () => Rec }).webkitSpeechRecognition;
  let rec: Rec | null = null;
  let hideTimer: number | undefined;

  const show = (h: string, r: string, keep = false) => {
    bubble.hidden = false;
    heard.textContent = h;
    replyEl.textContent = r;
    clearTimeout(hideTimer);
    if (!keep) hideTimer = window.setTimeout(() => { if (document.activeElement !== typed) bubble.hidden = true; }, 12000);
  };
  const speak = (text: string) => {
    if (!text || !settings.voice || !("speechSynthesis" in window)) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-CA";
    speechSynthesis.speak(u);
  };

  async function ask(text: string) {
    show(`“${text}”`, "…", true);
    try {
      const r = await fetch("/api/assistant", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text }) });
      const out = r.ok ? await r.json() : { reply: "The dash didn't answer.", actions: [] };
      show(`“${text}”`, out.reply);
      speak(out.reply);
      for (const a of out.actions ?? []) runAction(a);
    } catch {
      show(`“${text}”`, "Can't reach the dash computer.");
    }
  }

  form.onsubmit = (e) => {
    e.preventDefault();
    const t = typed.value.trim();
    typed.value = "";
    if (t) ask(t);
  };

  button.onclick = () => {
    if ("speechSynthesis" in window) { speechSynthesis.cancel(); speechSynthesis.speak(new SpeechSynthesisUtterance("")); } // unlock iPad speech
    if (rec) { rec.stop(); return; }
    if (!window.isSecureContext) {
      show("", "The microphone needs the dash opened over HTTPS. See the README: just https-cert. You can type below.", true);
      return;
    }
    if (!SR) { show("", "This browser has no speech recognition. You can type below.", true); return; }
    rec = new SR();
    rec.lang = "en-CA";
    rec.interimResults = true;
    rec.continuous = false;
    let finalText = "";
    button.classList.add("listening");
    show("Listening…", "", true);
    rec.onresult = (e) => {
      let text = "";
      for (let i = 0; i < e.results.length; i++) {
        text += e.results[i][0].transcript;
        if (e.results[i].isFinal) finalText = text;
      }
      heard.textContent = text;
    };
    rec.onerror = (e) => {
      const why = e.error === "not-allowed" ? "Microphone permission was refused. Allow it for this site in Safari settings."
        : e.error === "network" ? "Speech recognition needs a connection." : e.error === "no-speech" ? "I didn't hear anything." : `Speech error: ${e.error}`;
      show("", why);
    };
    rec.onend = () => {
      button.classList.remove("listening");
      rec = null;
      const t = finalText.trim() || heard.textContent?.replace(/^Listening…$/, "").trim();
      if (t) ask(t);
    };
    rec.start();
  };
}
