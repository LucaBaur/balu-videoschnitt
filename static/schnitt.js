"use strict";
// Balu-Videoschnitt - Editor im Browser. Zeichnet Markierungen auf einer
// Canvas-Ebene ueber dem Video (Koordinaten in Ausgabe-Pixeln, 1280x720),
// exportiert sie beim Rendern als transparente PNGs und schickt sie mit den
// Schnittzeiten ans lokale Backend.

const OUT_W = 1280, OUT_H = 720;
const $ = (s) => document.querySelector(s);

const S = {
  info: null,               // ffprobe-Infos der Quelle
  spiel: null,              // {art, gegner, datum} - gilt fuer alle Szenen dieser Aufnahme
  in_s: 0, out_s: 0,
  werkzeug: "auswahl",
  farbe: "#ff3b30",
  dicke: 6,
  formen: [],               // {id, typ, scope, x1,y1,x2,y2, farbe, dicke, text}
  standbilder: [],          // {id, bei_s, dauer_s}
  scope: "clip",            // "clip" oder Standbild-id
  auswahlId: null,
  render: null,             // Ergebnis von /api/render
};
let naechsteId = 1;
const neueId = () => "f" + (naechsteId++);

const video = $("#video");
const canvas = $("#canvas");
const ctx = canvas.getContext("2d");

const FARBEN = ["#ff3b30", "#ffcc00", "#34c759", "#0a84ff", "#ffffff", "#000000"];

// ------------------------------------------------------------- Start -------
init();

async function init() {
  try {
    const st = await (await fetch("/api/status")).json();
    $("#status-ff").textContent = "Server: " + st.balu_url;
    if (!st.ffmpeg || !st.ffprobe) $("#ffmpeg-warnung").hidden = false;
  } catch (_) {}

  const farbBox = $("#wz-farben");
  FARBEN.forEach((f, i) => {
    const b = document.createElement("button");
    b.className = "farbe" + (i === 0 ? " aktiv" : "");
    b.style.background = f;
    b.addEventListener("click", () => {
      S.farbe = f;
      document.querySelectorAll("#wz-farben .farbe").forEach((x) => x.classList.remove("aktiv"));
      b.classList.add("aktiv");
      if (S.auswahlId) { formNach(S.auswahlId).farbe = f; zeichne(); }
    });
    farbBox.appendChild(b);
  });

  document.querySelectorAll("#wz-tools button").forEach((b) => {
    b.addEventListener("click", () => {
      S.werkzeug = b.dataset.wz;
      document.querySelectorAll("#wz-tools button").forEach((x) => x.classList.remove("aktiv"));
      b.classList.add("aktiv");
    });
  });

  $("#dicke").addEventListener("input", (e) => {
    S.dicke = +e.target.value;
    if (S.auswahlId) { formNach(S.auswahlId).dicke = S.dicke; zeichne(); }
  });

  $("#datei-waehlen").addEventListener("click", dateiWaehlen);
  $("#laden").addEventListener("click", quelleLaden);
  $("#pfad").addEventListener("keydown", (e) => { if (e.key === "Enter") quelleLaden(); });
  $("#spiel-frage").addEventListener("submit", (e) => { e.preventDefault(); spielUebernehmen(); });
  $("#spiel-aendern").addEventListener("click", spielAendern);
  $("#kader-neu").addEventListener("click", ladeKader);
  $("#playpause").addEventListener("click", () => (video.paused ? video.play() : video.pause()));
  $("#frame-zur").addEventListener("click", () => frameSchritt(-1));
  $("#frame-vor").addEventListener("click", () => frameSchritt(1));
  $("#scrub").addEventListener("input", (e) => {
    if (S.info) video.currentTime = (+e.target.value / 100) * (S.info.dauer || video.duration || 0);
  });
  $("#set-in").addEventListener("click", () => { S.in_s = video.currentTime; if (S.out_s <= S.in_s) S.out_s = Math.min((S.info.dauer || 0), S.in_s + 5); ioAnzeige(); });
  $("#set-out").addEventListener("click", () => { S.out_s = Math.max(video.currentTime, S.in_s + 0.1); ioAnzeige(); });
  $("#add-standbild").addEventListener("click", standbildHinzu);
  $("#scope-clip").addEventListener("click", () => setScope("clip"));
  $("#del-sel").addEventListener("click", auswahlLoeschen);
  $("#rendern").addEventListener("click", rendern);
  $("#senden").addEventListener("click", senden);

  video.addEventListener("loadedmetadata", () => {
    S.in_s = 0;
    S.out_s = S.info ? S.info.dauer : video.duration;
    ioAnzeige();
    passeCanvas();
    zeitAnzeige();
  });
  video.addEventListener("timeupdate", zeitAnzeige);
  window.addEventListener("resize", passeCanvas);

  canvas.addEventListener("pointerdown", zeigerAb);
  canvas.addEventListener("pointermove", zeigerBeweg);
  canvas.addEventListener("pointerup", zeigerAuf);
  canvas.addEventListener("pointercancel", zeigerAuf);
  document.addEventListener("keydown", (e) => {
    if (["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
    if (e.key === " ") { e.preventDefault(); video.paused ? video.play() : video.pause(); }
    else if (e.key === ",") frameSchritt(-1);
    else if (e.key === ".") frameSchritt(1);
    else if (e.key === "Delete" || e.key === "Backspace") auswahlLoeschen();
  });

  ladeKader();
  scopeHinweis();
}

// ------------------------------------------------------------- Quelle -----
async function dateiWaehlen() {
  $("#probe").textContent = "Dateiauswahl offen…";
  try {
    const r = await fetch("/api/datei-dialog", { method: "POST" });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    if (!d.pfad) { $("#probe").textContent = "abgebrochen"; return; }
    $("#pfad").value = d.pfad;
    quelleLaden();
  } catch (e) {
    $("#probe").textContent = "Fehler: " + e.message;
  }
}

async function quelleLaden() {
  const pfad = $("#pfad").value.trim();
  if (!pfad) return;
  $("#probe").textContent = "lädt…";
  try {
    const r = await fetch("/api/quelle", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ pfad }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    S.info = d.info;
    const name = pfad.split(/[\\/]/).pop();
    $("#probe").textContent =
      `${name} — ${d.info.breite}×${d.info.hoehe}, ${d.info.fps} fps, ${fmtZeit(d.info.dauer)}` +
      (d.info.hat_audio ? ", mit Ton" : ", ohne Ton");
    $("#editor").hidden = false;
    video.src = "/api/quelle/stream?" + Date.now();
    video.load();
    if (!S.spiel) spielFrageZeigen();
  } catch (e) {
    $("#probe").textContent = "Fehler: " + e.message;
  }
}

// ------------------------------------------------------------- Spiel ------
function spielFrageZeigen() {
  const d = $("#spiel-datum");
  if (!d.value) d.value = new Date().toISOString().slice(0, 10);
  $("#spiel-frage").hidden = false;
  $("#spiel-kopf").hidden = true;
  $("#spiel-gegner").focus();
}

function spielUebernehmen() {
  S.spiel = {
    art: $("#spiel-art").value,
    gegner: $("#spiel-gegner").value.trim(),
    datum: $("#spiel-datum").value,
  };
  $("#spiel-kopf-text").textContent = spielText()
    + (S.spiel.datum ? " · " + S.spiel.datum : "");
  $("#spiel-frage").hidden = true;
  $("#spiel-kopf").hidden = false;
}

function spielAendern() {
  $("#spiel-frage").hidden = false;
  $("#spiel-kopf").hidden = true;
}

function spielText() {
  if (!S.spiel) return "";
  return S.spiel.art + (S.spiel.gegner ? " gegen " + S.spiel.gegner : "");
}

// ------------------------------------------------------------- Transport --
function frameSchritt(richtung) {
  if (!video.duration) return;
  const fps = (S.info && S.info.fps) || 30;
  video.pause();
  video.currentTime = Math.min(video.duration, Math.max(0, video.currentTime + richtung / fps));
}

function zeitAnzeige() {
  const d = (S.info && S.info.dauer) || video.duration || 0;
  $("#zeit").textContent = `${fmtZeit(video.currentTime)} / ${fmtZeit(d)}`;
  $("#scrub").value = d ? (video.currentTime / d) * 100 : 0;
}

function ioAnzeige() {
  $("#io-anzeige").textContent =
    `Szene: ${fmtZeit(S.in_s)} – ${fmtZeit(S.out_s)}  (${(S.out_s - S.in_s).toFixed(1)} s)`;
}

function fmtZeit(s) {
  s = Math.max(0, s || 0);
  const m = Math.floor(s / 60);
  const r = (s - m * 60);
  return `${m}:${r.toFixed(r < 10 ? 2 : 1).padStart(r < 10 ? 5 : 4, "0")}`;
}

// ------------------------------------------------------------- Canvas -----
function passeCanvas() {
  // interne Groesse bleibt fix (Ausgabeaufloesung); CSS skaliert.
  zeichne();
}

function zeigerPos(e) {
  const r = canvas.getBoundingClientRect();
  return {
    x: ((e.clientX - r.left) / r.width) * OUT_W,
    y: ((e.clientY - r.top) / r.height) * OUT_H,
  };
}

let zieh = null;     // {modus:"neu"|"move", startPos, formId}

function zeigerAb(e) {
  canvas.setPointerCapture(e.pointerId);
  const p = zeigerPos(e);

  if (S.werkzeug === "text") {
    const t = prompt("Text im Bild:");
    if (t && t.trim()) {
      const f = grundForm("text", p);
      f.text = t.trim();
      S.formen.push(f);
      S.auswahlId = f.id;
      zeichne();
    }
    return;
  }

  if (S.werkzeug === "auswahl") {
    const treffer = trefferForm(p);
    S.auswahlId = treffer ? treffer.id : null;
    if (treffer) zieh = { modus: "move", startPos: p, formId: treffer.id };
    zeichne();
    return;
  }

  // Pfeil / Kreis: neue Form aufziehen
  const f = grundForm(S.werkzeug, p);
  S.formen.push(f);
  S.auswahlId = f.id;
  zieh = { modus: "neu", startPos: p, formId: f.id };
  zeichne();
}

function zeigerBeweg(e) {
  if (!zieh) return;
  const p = zeigerPos(e);
  const f = formNach(zieh.formId);
  if (!f) return;
  if (zieh.modus === "neu") {
    f.x2 = p.x; f.y2 = p.y;
  } else {
    const dx = p.x - zieh.startPos.x, dy = p.y - zieh.startPos.y;
    zieh.startPos = p;
    f.x1 += dx; f.y1 += dy; f.x2 += dx; f.y2 += dy;
  }
  zeichne();
}

function zeigerAuf(e) {
  if (zieh && zieh.modus === "neu") {
    const f = formNach(zieh.formId);
    if (f && Math.hypot(f.x2 - f.x1, f.y2 - f.y1) < 6) {
      S.formen = S.formen.filter((x) => x.id !== f.id);   // Klick ohne Ziehen
      S.auswahlId = null;
    }
  }
  zieh = null;
  try { canvas.releasePointerCapture(e.pointerId); } catch (_) {}
  zeichne();
}

function grundForm(typ, p) {
  return {
    id: neueId(), typ, scope: S.scope,
    x1: p.x, y1: p.y, x2: p.x, y2: p.y,
    farbe: S.farbe, dicke: S.dicke, text: "",
  };
}

const formNach = (id) => S.formen.find((f) => f.id === id);
const sichtbareFormen = () => S.formen.filter((f) => f.scope === S.scope);

function trefferForm(p) {
  const liste = sichtbareFormen();
  for (let i = liste.length - 1; i >= 0; i--) {
    const f = liste[i];
    const minX = Math.min(f.x1, f.x2) - 14, maxX = Math.max(f.x1, f.x2) + 14;
    const minY = Math.min(f.y1, f.y2) - 14, maxY = Math.max(f.y1, f.y2) + 14;
    if (p.x >= minX && p.x <= maxX && p.y >= minY && p.y <= maxY) return f;
  }
  return null;
}

function auswahlLoeschen() {
  if (!S.auswahlId) return;
  S.formen = S.formen.filter((f) => f.id !== S.auswahlId);
  S.auswahlId = null;
  zeichne();
}

function zeichne(zielCtx, formen, mitAuswahl = true) {
  const c = zielCtx || ctx;
  const fs = formen || sichtbareFormen();
  if (!zielCtx) c.clearRect(0, 0, OUT_W, OUT_H);
  for (const f of fs) {
    c.strokeStyle = f.farbe;
    c.fillStyle = f.farbe;
    c.lineWidth = f.dicke;
    c.lineCap = "round";
    c.lineJoin = "round";
    if (f.typ === "pfeil") pfeil(c, f);
    else if (f.typ === "kreis") kreis(c, f);
    else if (f.typ === "text") textForm(c, f);
    if (mitAuswahl && !zielCtx && f.id === S.auswahlId) auswahlRahmen(c, f);
  }
}

function pfeil(c, f) {
  const kopf = Math.max(14, f.dicke * 3.2);
  const w = Math.atan2(f.y2 - f.y1, f.x2 - f.x1);
  c.beginPath();
  c.moveTo(f.x1, f.y1);
  c.lineTo(f.x2, f.y2);
  c.stroke();
  c.beginPath();
  c.moveTo(f.x2, f.y2);
  c.lineTo(f.x2 - kopf * Math.cos(w - Math.PI / 7), f.y2 - kopf * Math.sin(w - Math.PI / 7));
  c.lineTo(f.x2 - kopf * Math.cos(w + Math.PI / 7), f.y2 - kopf * Math.sin(w + Math.PI / 7));
  c.closePath();
  c.fill();
}

function kreis(c, f) {
  const cx = (f.x1 + f.x2) / 2, cy = (f.y1 + f.y2) / 2;
  const rx = Math.max(2, Math.abs(f.x2 - f.x1) / 2), ry = Math.max(2, Math.abs(f.y2 - f.y1) / 2);
  c.beginPath();
  c.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
  c.stroke();
}

function textForm(c, f) {
  const groesse = Math.round(f.dicke * 4 + 16);
  c.font = `700 ${groesse}px system-ui, "Segoe UI", sans-serif`;
  c.textBaseline = "top";
  c.lineWidth = Math.max(3, groesse / 8);
  c.strokeStyle = "rgba(0,0,0,0.55)";
  c.strokeText(f.text, f.x1, f.y1);
  c.fillStyle = f.farbe;
  c.fillText(f.text, f.x1, f.y1);
}

function auswahlRahmen(c, f) {
  const minX = Math.min(f.x1, f.x2), maxX = Math.max(f.x1, f.x2);
  const minY = Math.min(f.y1, f.y2), maxY = Math.max(f.y1, f.y2);
  c.save();
  c.setLineDash([8, 6]);
  c.strokeStyle = "#0a84ff";
  c.lineWidth = 2;
  c.strokeRect(minX - 8, minY - 8, (maxX - minX) + 16, (maxY - minY) + 16);
  c.restore();
}

// ------------------------------------------------------------- Standbilder
function standbildHinzu() {
  if (!S.info) return;
  const bei = video.currentTime - S.in_s;
  if (bei <= 0.05 || bei >= (S.out_s - S.in_s) - 0.05) {
    alert("Das Standbild muss innerhalb der Szene liegen (nicht ganz am Anfang oder Ende).");
    return;
  }
  const sb = { id: "s" + (naechsteId++), bei_s: bei, dauer_s: 3 };
  S.standbilder.push(sb);
  S.standbilder.sort((a, b) => a.bei_s - b.bei_s);
  setScope(sb.id);
  renderStandbildListe();
}

function renderStandbildListe() {
  const ul = $("#standbild-liste");
  ul.innerHTML = "";
  for (const sb of S.standbilder) {
    const li = document.createElement("li");
    if (S.scope === sb.id) li.classList.add("aktiv");
    const z = document.createElement("span");
    z.className = "sb-zeit";
    z.textContent = `bei ${fmtZeit(sb.bei_s)}`;
    z.title = "Markierungen dieses Standbilds bearbeiten";
    z.addEventListener("click", () => { video.currentTime = S.in_s + sb.bei_s; setScope(sb.id); });
    const d = document.createElement("input");
    d.type = "number"; d.min = "0.5"; d.max = "15"; d.step = "0.5"; d.value = sb.dauer_s;
    d.title = "Dauer in Sekunden";
    d.addEventListener("change", () => { sb.dauer_s = Math.max(0.5, +d.value || 3); });
    const x = document.createElement("button");
    x.className = "gefahr"; x.textContent = "✕";
    x.addEventListener("click", () => {
      S.standbilder = S.standbilder.filter((s) => s.id !== sb.id);
      S.formen = S.formen.filter((f) => f.scope !== sb.id);
      if (S.scope === sb.id) setScope("clip");
      renderStandbildListe();
    });
    li.append(z, d, document.createTextNode("s"), x);
    ul.appendChild(li);
  }
}

function setScope(scope) {
  S.scope = scope;
  S.auswahlId = null;
  $("#scope-clip").classList.toggle("aktiv", scope === "clip");
  renderStandbildListe();
  scopeHinweis();
  zeichne();
}

function scopeHinweis() {
  const el = $("#scope-hinweis");
  if (S.scope === "clip") el.textContent = "Markierungen gelten für den ganzen Clip.";
  else {
    const sb = S.standbilder.find((s) => s.id === S.scope);
    el.textContent = sb ? `Markierungen gelten für das Standbild bei ${fmtZeit(sb.bei_s)}.` : "";
  }
}

// ------------------------------------------------------------- Kader ------
async function ladeKader() {
  const box = $("#kader");
  const neu = $("#kader-neu");
  box.innerHTML = '<span class="hinweis">lädt…</span>';
  neu.hidden = true;
  try {
    const r = await fetch("/api/kader");
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    box.innerHTML = "";
    const vorher = new Set(gewaehlteSpieler());
    for (const sp of d) {
      const lab = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.value = sp.id;
      if (vorher.has(sp.id)) cb.checked = true;
      lab.append(cb, document.createTextNode(" " + sp.name));
      box.appendChild(lab);
    }
    if (!d.length) box.innerHTML = '<span class="hinweis">Kein Kader gefunden.</span>';
  } catch (e) {
    box.innerHTML = `<span class="hinweis">Kader nicht geladen: ${e.message}</span>`;
    neu.hidden = false;
  }
}

function gewaehlteSpieler() {
  return [...document.querySelectorAll("#kader input:checked")].map((c) => +c.value);
}

// ------------------------------------------------------------- Rendern ----
function pngFuer(formen) {
  const c = document.createElement("canvas");
  c.width = OUT_W; c.height = OUT_H;
  const cc = c.getContext("2d");
  zeichne(cc, formen, false);
  return c.toDataURL("image/png");
}

async function rendern() {
  if (!S.info) return;
  if (S.out_s - S.in_s <= 0.2) { alert("Erst Start- und Endpunkt der Szene setzen."); return; }
  const dauer = S.out_s - S.in_s;

  const annots = [];
  const clipFormen = S.formen.filter((f) => f.scope === "clip");
  if (clipFormen.length) annots.push({ png_b64: pngFuer(clipFormen), von_s: 0, bis_s: dauer });

  const standbilder = S.standbilder
    .filter((sb) => sb.bei_s > 0.05 && sb.bei_s < dauer - 0.05)
    .map((sb) => ({
      bei_s: sb.bei_s, dauer_s: sb.dauer_s,
      png_b64: pngFuer(S.formen.filter((f) => f.scope === sb.id)),
    }));

  const btn = $("#rendern");
  btn.disabled = true;
  $("#render-status").textContent = "Rendert… (kann bei langen Szenen dauern)";
  $("#log").textContent = "";
  try {
    const r = await fetch("/api/render", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({
        in_s: S.in_s, out_s: S.out_s, fps: (S.info.fps || 30),
        ton: $("#ton").checked, annots, standbilder,
      }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    S.render = d;
    $("#render-status").textContent =
      `Fertig: ${d.datei} · ${d.dauer_s}s · ${(d.groesse / 1e6).toFixed(1)} MB`;
    const a = $("#ausgabe");
    a.src = "/api/render/" + encodeURIComponent(d.datei) + "?" + Date.now();
    a.hidden = false;
    $("#senden").hidden = false;
  } catch (e) {
    $("#render-status").textContent = "Fehler: " + e.message;
    $("#log").textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

async function senden() {
  if (!S.render) return;
  const titel = $("#titel").value.trim();
  if (!titel) { alert("Bitte einen Titel für die Szene eintragen."); return; }
  if (!S.spiel) { alert("Bitte oben eintragen, was das für ein Spiel ist."); spielAendern(); return; }

  // Der Spiel-Kontext wandert in die Notiz, damit die Spielerin ihn sieht.
  const teile = [spielText(), $("#notiz").value.trim()].filter(Boolean);
  const btn = $("#senden");
  btn.disabled = true;
  $("#senden-status").textContent = "Lädt hoch…";
  try {
    const r = await fetch("/api/veroeffentlichen", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({
        datei: S.render.datei, titel,
        notiz: teile.join(" – "),
        spiel_datum: (S.spiel && S.spiel.datum) || "",
        spieler_ids: gewaehlteSpieler(),
      }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    const namen = (d.spieler || []).map((s) => s.name).join(", ") || "niemandem";
    $("#senden-status").textContent = `Gesendet ✓ – „${d.titel}" ist jetzt für ${namen} sichtbar.`;
  } catch (e) {
    $("#senden-status").textContent = "Fehler: " + e.message;
  } finally {
    btn.disabled = false;
  }
}
