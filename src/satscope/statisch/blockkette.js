// Das Live-Band der Bloecke. Kein Framework, kein Bauschritt, kein CDN:
// mempool.space laedt fuer dieselbe Reihe 4,6 MB JavaScript. Kein sichtbarer
// Satz steht hier - alle Texte kommen aus dem Katalog und reisen in data-texte
// mit; und kein Wert wird je als HTML eingesetzt, immer nur als textContent.
(function () {
  "use strict";

  var wurzel = document.getElementById("blockkette");
  if (!wurzel) { return; }

  var T = {};
  try { T = JSON.parse(wurzel.getAttribute("data-texte") || "{}"); } catch (e) { T = {}; }
  var QUELLE = wurzel.getAttribute("data-quelle") || "/api/kette";
  var ZIEL = wurzel.getAttribute("data-blockziel") || "";
  // Dieselbe Quelle wie serverseitig, damit Zahlen auf beiden Seiten gleich
  // aussehen: deutsch 1.234,56 gegen englisch 1,234.56.
  var SPRACHE = document.documentElement.lang === "de" ? "de-DE" : "en-US";
  var STRICH = T.fehlt || "–";
  var TAKT = 5000;
  // Farbstufen in sat/vB, absolut und nicht relativ zum Fenster: eine Skala,
  // die sich am eigenen Bild ausrichtet, zeigt IMMER einen gruenen und einen
  // roten Block - auch wenn alle zwoelf gleich teuer waren.
  var STUFEN = [1, 3, 8, 20, 50];

  var reihe = document.getElementById("kette-reihe");
  var rahmen = document.getElementById("kette-rahmen");
  var warte = document.getElementById("kette-warte");
  var tafel = document.getElementById("kette-tafel");
  var muster = document.getElementById("kette-muster");
  var zusammen = document.getElementById("kette-zusammen");

  var elemente = {};    // Hoehe -> Element
  var bekannt = [];     // Hoehen im DOM, aelteste zuerst
  var DATEN = null, gezeigt = null, aktiv = null;
  var versatz = 0;      // Serveruhr minus Browseruhr, in Sekunden
  var schlecht = 0, wecker = null, laeuft = false;

  // Nicht einmalig auslesen: die Einstellung kann sich im Betrieb aendern.
  var ruhelage = window.matchMedia
    ? matchMedia("(prefers-reduced-motion: reduce)") : null;
  function ruhig() { return !!(ruhelage && ruhelage.matches); }

  // NUR benannte Platzhalter - die Wortstellung weicht zwischen den Sprachen
  // ab, {0} waere schon falsch.
  function t(schluessel, werte) {
    var s = T[schluessel];
    if (s === undefined) { return "!" + schluessel + "!"; }
    if (!werte) { return s; }
    return s.replace(/\{(\w+)\}/g, function (_, n) {
      return werte[n] !== undefined ? werte[n] : "{" + n + "}";
    });
  }

  function zahl(w, stellen) {
    if (w === null || w === undefined || isNaN(w)) { return STRICH; }
    stellen = stellen || 0;
    return Number(w).toLocaleString(SPRACHE,
      { minimumFractionDigits: stellen, maximumFractionDigits: stellen });
  }

  // sat/vB: unter zehn braucht es Nachkommastellen, darueber sind sie Ballast.
  function geb(w) {
    if (w === null || w === undefined || isNaN(w)) { return STRICH; }
    return Number(w).toLocaleString(SPRACHE, { maximumFractionDigits: w < 10 ? 2 : 0 });
  }

  function btc(sat, stellen) { return sat === null ? STRICH : zahl(sat / 1e8, stellen); }

  function jetzt() { return Date.now() / 1e3 + versatz; }

  function alter(sek) {
    if (sek === null || isNaN(sek)) { return STRICH; }
    if (sek < 10) { return t("jetzt"); }
    if (sek < 60) { return t("sek", { n: zahl(Math.floor(sek)) }); }
    var min = Math.floor(sek / 60);
    if (min < 60) { return t("min", { n: zahl(min) }); }
    return t("std", { h: zahl(Math.floor(min / 60)), m: zahl(min % 60) });
  }

  function stufe(satvb) {
    if (satvb === null || satvb === undefined) { return "0"; }
    var i = 0;
    while (i < STUFEN.length && satvb >= STUFEN[i]) { i++; }
    return String(i);
  }

  function setz(el, wahl, text) {
    var k = el.querySelector(wahl);
    if (k) { k.textContent = text; }
    return k;
  }

  function setzAlle(el, vorsatz, werte) {
    Object.keys(werte).forEach(function (k) { setz(el, vorsatz + k, werte[k]); });
  }

  function uhrstellen(el, wahl, zeit) {
    var k = setz(el, wahl, alter(zeit === null ? null : jetzt() - zeit));
    if (k) { k.setAttribute("data-zeit", zeit === null ? "" : zeit); }
  }

  // Ein Block, in Text uebersetzt. Kaestchen und Tafel teilen sich das, damit
  // dieselbe Zahl nicht an zwei Stellen verschieden gerundet wird.
  function worte(b) {
    var g = (DATEN && DATEN.blockgewicht) || 4e6;
    return {
      hoehe: zahl(b.hoehe),
      med: geb(b.gebuehr_median),
      spanne: (b.gebuehr_min === null || b.gebuehr_max === null) ? "" :
        t("spanne", { min: geb(b.gebuehr_min), max: geb(b.gebuehr_max) }),
      txs: b.txs === null ? t("keine") : t("txs", { n: zahl(b.txs) }),
      mb: b.groesse === null ? "" : t("mb", { n: zahl(b.groesse / 1e6, 2) }),
      voll: b.gewicht === null ? "" : t("voll", { n: zahl(b.gewicht / (g / 100), 1) }),
      anteil: b.gewicht === null ? 0 : Math.min(100, b.gewicht / g * 100)
    };
  }

  function fuelle(el, b) {
    var w = worte(b);
    el.setAttribute("data-hoehe", b.hoehe);
    el.setAttribute("data-geb", stufe(b.gebuehr_median));
    el.setAttribute("tabindex", "0");
    if (ZIEL) {
      el.setAttribute("href", ZIEL + b.hoehe);
      el.setAttribute("title", t("oeffnen", { n: w.hoehe }));
    }
    setzAlle(el, ".kb-", {
      hoehe: w.hoehe, med: w.med, spanne: w.spanne, txs: w.txs, mb: w.mb,
      abzeichen: b.billigster ? "▾" : (b.teuerster ? "▴" : "")
    });
    uhrstellen(el, ".kb-alter", b.zeit);
    var balken = el.querySelector(".kb-balken i");
    if (balken) { balken.style.width = w.anteil.toFixed(1) + "%"; }
  }

  function mach(b) {
    var el = muster.content.firstElementChild.cloneNode(true);
    elemente[b.hoehe] = el;
    fuelle(el, b);
    return el;
  }

  function austragen(hoehe) {
    var el = elemente[hoehe];
    delete elemente[hoehe];
    if (!el) { return; }
    if (ruhig()) { el.remove(); return; }
    // Der Block links faellt nicht weg, er schrumpft - dadurch ruecken alle
    // anderen sichtbar nach links, ohne dass eine Breite gemessen werden muss.
    el.classList.add("kb-raus");
    setTimeout(function () { el.remove(); }, 600);
  }

  function zeichneBloecke(d) {
    var neue = d.bloecke.map(function (b) { return b.hoehe; });
    var frisch = neue.filter(function (x) { return bekannt.indexOf(x) < 0; });
    // Erster Aufbau, Rueckwaertssprung oder lange Pause: ohne Animation neu
    // setzen. Zwoelf einschwebende Bloecke waeren Krach, kein Hinweis.
    if (!bekannt.length || frisch.length > 2 || ruhig()) {
      reihe.textContent = "";
      elemente = {};
      aktiv = null;
      d.bloecke.forEach(function (b) { reihe.appendChild(mach(b)); });
    } else {
      d.bloecke.forEach(function (b) {
        var el = elemente[b.hoehe];
        if (el) { fuelle(el, b); return; }
        el = mach(b);
        reihe.appendChild(el);
        el.classList.add("kb-neu");
        setTimeout(function () { el.classList.remove("kb-neu"); }, 1400);
      });
      bekannt.forEach(function (x) { if (neue.indexOf(x) < 0) { austragen(x); } });
    }
    bekannt = neue;
    // Wer selbst nach links gerollt hat, wird nicht zurueckgerissen.
    if (rahmen.scrollWidth - rahmen.clientWidth - rahmen.scrollLeft < 80) {
      rahmen.scrollLeft = rahmen.scrollWidth;
    }
  }

  function zeichneWarte(m) {
    if (!m) { return; }
    var erste = (m.schaetzung && m.schaetzung.length) ? m.schaetzung[0].satvb : null;
    warte.setAttribute("data-geb", stufe(erste !== null ? erste : m.min_gebuehr));
    var f = warte.querySelector(".kb-fuell i");
    if (f) { f.style.height = (m.fuellung === null ? 0 : m.fuellung * 100) + "%"; }
    setzAlle(warte, ".kw-", {
      blocks: m.blockaequivalent === null ? STRICH
        : t("rueckstau", { n: zahl(m.blockaequivalent, 1) }),
      eintritt: erste !== null ? t("schaetzung", { n: geb(erste) })
        : (m.min_gebuehr === null ? STRICH : t("eintritt", { n: geb(m.min_gebuehr) })),
      anzahl: m.anzahl === null ? STRICH
        : (m.anzahl === 0 ? t("leer") : t("warten", { n: zahl(m.anzahl) })),
      mb: m.bytes === null ? STRICH : t("mb", { n: zahl(m.bytes / 1e6, 1) })
    });
    // Die weiteren Ziele in den Tooltip; im Kaestchen waeren sie zu viel.
    warte.setAttribute("title", (m.schaetzung || []).map(function (s) {
      return t("schaetzung_ziel", { ziel: zahl(s.ziel), n: geb(s.satvb) });
    }).join("\n"));
  }

  function zeigeTafel(hoehe) {
    var b = null;
    if (!DATEN) { return; }
    DATEN.bloecke.forEach(function (x) { if (x.hoehe === hoehe) { b = x; } });
    if (!b) { return; }
    gezeigt = hoehe;
    if (aktiv) { aktiv.classList.remove("kb-aktiv"); }
    aktiv = elemente[hoehe] || null;
    if (aktiv) { aktiv.classList.add("kb-aktiv"); }

    var w = worte(b);
    var mitte = (DATEN.fenster || {}).median_mitte;
    var satz = b.billigster ? t("billigster", { n: zahl(bekannt.length) })
      : (b.teuerster ? t("teuerster", { n: zahl(bekannt.length) }) : "");
    if (mitte !== null && mitte !== undefined) {
      satz = (satz ? satz + " · " : "") + t("fenstermitte", { n: geb(mitte) });
    }
    tafel.setAttribute("data-geb", stufe(b.gebuehr_median));
    setzAlle(tafel, ".kt-", {
      hoehe: w.hoehe,
      hash: b.hash ? b.hash.slice(0, 20) + "…" : STRICH,
      median: b.gebuehr_median === null ? STRICH : t("median", { n: w.med }),
      spanne: w.spanne,
      mb: w.mb || STRICH,
      voll: w.voll,
      txs: w.txs,
      einaus: b.ein === null ? "" : t("einaus", { ein: zahl(b.ein), aus: zahl(b.aus) }),
      belohnung: b.belohnung === null ? STRICH : t("belohnung", { n: btc(b.belohnung, 3) }),
      gebuehren: b.gebuehren === null ? "" : t("gebuehren", { n: btc(b.gebuehren, 4) }),
      // Ein negativer Abstand ist echt (ein Miner darf innerhalb der
      // Median-Zeit-Regel zurueckdatieren). Gezeigt wird er nicht - er saehe
      // wie ein Fehler aus -, erfunden wird aber auch nichts.
      abstand: (b.abstand === null || b.abstand < 0) ? "" : (b.abstand < 60
        ? t("nachher_kurz") : t("nachher", { n: zahl(Math.round(b.abstand / 60)) })),
      satz: satz
    });
    uhrstellen(tafel, ".kt-alter", b.zeit);

    var stiele = tafel.querySelectorAll(".kt-perzentile i");
    var p = b.perzentile;
    var gipfel = p ? Math.max.apply(null, p) : 0;
    for (var i = 0; i < stiele.length; i++) {
      var v = p ? p[i] : null;
      // Wurzelskala: zieht ein einziger Eilzahler das 90. Perzentil hoch,
      // waeren die unteren vier Balken unsichtbar - und gerade die sagen, ob
      // man guenstig hineingekommen waere.
      stiele[i].style.height = (gipfel > 0 && v !== null)
        ? Math.max(5, Math.sqrt(v / gipfel) * 100) + "%" : "0";
      stiele[i].style.background = "var(--geb-" + stufe(v) + ")";
    }
  }

  // Das Alter laeuft im Browser weiter, ohne dass etwas nachgeladen wird.
  function tick() {
    var n = jetzt();
    var alle = wurzel.querySelectorAll("[data-zeit]");
    for (var i = 0; i < alle.length; i++) {
      var s = parseInt(alle[i].getAttribute("data-zeit"), 10);
      alle[i].textContent = isNaN(s) ? STRICH : alter(n - s);
    }
  }

  function zeichne(d) {
    DATEN = d;
    // Geht die Uhr im Wohnzimmer zwei Minuten vor, stuende hier sonst ein
    // Blockalter von "-2 Min." - das sieht nach einem Fehler aus.
    versatz = d.jetzt - Date.now() / 1e3;
    if (d.bloecke && d.bloecke.length) { zeichneBloecke(d); }
    zeichneWarte(d.mempool);

    var f = d.fenster || {};
    zusammen.textContent = (!f.gemessen || f.txs_gesamt === null) ? "" : t("zusammen", {
      b: zahl(f.gemessen), t: zahl(f.txs_gesamt),
      m: f.abstand_schnitt === null ? STRICH : zahl(f.abstand_schnitt / 60, 1) });

    // Die Tafel folgt der Spitze, solange niemand einen anderen Block ansieht.
    if (gezeigt === null || !elemente[gezeigt]) {
      gezeigt = bekannt.length ? bekannt[bekannt.length - 1] : null;
    }
    if (gezeigt !== null) { zeigeTafel(gezeigt); }
  }

  function planen(ms) {
    clearTimeout(wecker);
    wecker = setTimeout(hole, ms);
  }

  function hole() {
    if (laeuft) { return; }
    // Ein verstecktes Fenster fragt nicht: der Knoten gehoert dem Nutzer.
    if (document.hidden) { planen(TAKT); return; }
    laeuft = true;
    fetch(QUELLE, { cache: "no-store" }).then(function (a) {
      return a.ok ? a.json() : Promise.reject(a.status);
    }).then(function (d) {
      laeuft = false;
      schlecht = 0;
      wurzel.classList.toggle("kette-weg", !d.erreichbar);
      zeichne(d);
      planen(TAKT);
    }).catch(function () {
      laeuft = false;
      schlecht++;
      // Erst der zweite Fehlschlag wird gemeldet - ein einzelner Aussetzer
      // beim Abholen ist kein kaputter Knoten -, danach immer traeger fragen.
      if (schlecht > 1) { wurzel.classList.add("kette-weg"); }
      planen(Math.min(60000, TAKT * Math.pow(2, schlecht)));
    });
  }

  function aufBlock(e) {
    var el = e.target && e.target.closest ? e.target.closest(".kb") : null;
    if (el && el.hasAttribute("data-hoehe")) {
      zeigeTafel(parseInt(el.getAttribute("data-hoehe"), 10));
    }
  }

  reihe.addEventListener("mouseover", aufBlock);
  reihe.addEventListener("focusin", aufBlock);
  reihe.addEventListener("mouseleave", function () {
    if (bekannt.length) { zeigeTafel(bekannt[bekannt.length - 1]); }
  });
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) { hole(); }
  });

  setInterval(tick, 1000);
  hole();
})();
