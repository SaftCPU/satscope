// Das Blockband - die waagerechte Reihe im Zuschnitt von mempool.space.
//
// Kein Framework, kein CDN, kein Bauschritt: mempool.space laedt fuer dieselbe
// Reihe mehrere Megabyte JavaScript. Kein sichtbarer Satz steht hier - alle
// Texte reisen in data-texte mit, sonst braeche die Zweisprachigkeit genau
// dort, wo sie niemand nachprueft. Kein Wert wird je als HTML eingesetzt,
// immer nur als textContent.
//
// ZWEI QUELLEN, getrennt abgesichert:
//   /api/kette       bestaetigte Bloecke + Mempool-Kennzahlen (Pflicht)
//   /api/projektion  die kuenftigen Bloecke aus dem Gebuehrenhistogramm (kuer)
// Faellt die zweite aus oder gibt es sie noch nicht, zeigt das Band links
// EINE gestrichelte Kachel fuer die ganze Warteschlange - aus Zahlen, die
// /api/kette ohnehin liefert. Geraten wird nichts.
(function () {
  "use strict";

  var wurzel = document.getElementById("blockband");
  if (!wurzel) { return; }

  var T = {};
  try { T = JSON.parse(wurzel.getAttribute("data-texte") || "{}"); } catch (e) { T = {}; }
  var QUELLE = wurzel.getAttribute("data-quelle") || "/api/kette";
  var PROJ = wurzel.getAttribute("data-projektion") || "";
  var ZIEL = wurzel.getAttribute("data-blockziel") || "";
  // Dieselbe Quelle wie serverseitig: deutsch 1.234,56 gegen englisch 1,234.56.
  var SPRACHE = document.documentElement.lang === "de" ? "de-DE" : "en-US";
  var STRICH = T.fehlt || "–";
  var TAKT = 5000;
  // Farbstufen in sat/vB - ABSOLUT und nicht relativ zum gezeigten Fenster:
  // eine Skala am eigenen Bild zeigt IMMER einen blauen und einen roten Block,
  // auch wenn zwischen ihnen 0,2 sat/vB liegen.
  var STUFEN = [1, 3, 8, 20, 50];

  function nach(id) { return document.getElementById(id); }
  var rahmen = nach("bb-rahmen"), gMem = nach("bb-mempool"),
      gBlk = nach("bb-bloecke"), trenner = nach("bb-trenner"),
      lage = nach("bb-lage"), muster = nach("bb-muster");
  if (!rahmen || !gMem || !gBlk || !muster) { return; }

  var kacheln = {};          // Hoehe -> Element
  var bekannt = [];          // Hoehen im DOM, NEUESTE zuerst
  var gewicht = 4000000;     // Blockgewicht, kommt mit den Daten
  var versatz = 0;           // Serveruhr minus Browseruhr, in Sekunden
  var schlecht = 0, projFehl = 0, wecker = null, laeuft = false;
  var anker = -1;            // zuletzt selbst gesetzte Bildlaufposition

  // Nicht einmalig auslesen: die Einstellung kann sich im Betrieb aendern.
  var ruhelage = window.matchMedia
    ? matchMedia("(prefers-reduced-motion: reduce)") : null;
  function ruhig() { return !!(ruhelage && ruhelage.matches); }

  // NUR benannte Platzhalter - die Wortstellung weicht ab, {0} waere falsch.
  function t(k, werte) {
    var s = T[k];
    if (s === undefined) { return "!" + k + "!"; }
    if (!werte) { return s; }
    return s.replace(/\{(\w+)\}/g, function (_, n) {
      return werte[n] !== undefined ? werte[n] : "{" + n + "}";
    });
  }

  function da(w) {
    return w !== null && w !== undefined && !isNaN(w);
  }

  function zahl(w, s) {
    if (!da(w)) { return STRICH; }
    return Number(w).toLocaleString(SPRACHE,
      { minimumFractionDigits: s || 0, maximumFractionDigits: s || 0 });
  }

  // sat/vB: unter zehn braucht es Nachkommastellen, darueber sind sie Ballast.
  function geb(w) {
    if (!da(w)) { return STRICH; }
    return Number(w).toLocaleString(SPRACHE, { maximumFractionDigits: w < 10 ? 2 : 0 });
  }

  function jetzt() { return Date.now() / 1e3 + versatz; }

  function alter(sek) {
    if (!da(sek)) { return STRICH; }
    if (sek < 10) { return t("jetzt"); }
    if (sek < 60) { return t("sek", { n: zahl(Math.floor(sek)) }); }
    var min = Math.floor(sek / 60);
    if (min < 60) { return t("min", { n: zahl(min) }); }
    return t("std", { h: zahl(Math.floor(min / 60)), m: zahl(min % 60) });
  }

  // Leere Zeichenkette statt "0": ohne Gebuehr gibt es keine Farbe, und grau
  // ist die ehrliche Antwort. Die Stufe waechst mit dem Preis.
  function stufe(satvb) {
    if (!da(satvb)) { return ""; }
    var i = 0;
    while (i < STUFEN.length && satvb >= STUFEN[i]) { i++; }
    return String(i);
  }

  function setz(el, wahl, text) {
    var k = el.querySelector(wahl);
    if (k) { k.textContent = text; }
    return k;
  }

  // ------------------------------------------------------------- Umformen
  // Jede Quelle wird auf DIESELBEN acht Felder gebracht. Danach weiss das
  // Zeichnen nicht mehr, ob es einen bestaetigten, einen kuenftigen oder den
  // Ersatz-Wartestapel malt - und kann deshalb nur eine Sorte Fehler machen.
  //   art    block | proj | warte
  //   rate   Gebuehr fuer Farbe und grosse Zahl (sat/vB) oder null
  //   gross  fertiger Text der grossen Zahl
  //   min/max  Gebuehrenspanne oder null
  //   drei   dritte Zeile (Transaktionen bzw. Fuellung)
  //   mb     vierte Zeile (Groesse)
  //   fuell  Fuellung in Prozent oder null
  //   zeit   Blockzeit fuer den Alter-Ticker oder null
  //   unten/unten2  die zwei Zeilen unter dem Wuerfel
  function ausBlock(b) {
    return {
      art: "block", schluessel: b.hoehe,
      rate: b.gebuehr_median,
      gross: da(b.gebuehr_median) ? t("rate", { n: geb(b.gebuehr_median) }) : STRICH,
      min: b.gebuehr_min, max: b.gebuehr_max,
      drei: da(b.txs) ? t("txs", { n: zahl(b.txs) }) : t("keine"),
      mb: da(b.groesse) ? t("mb", { n: zahl(b.groesse / 1e6, 2) }) : STRICH,
      // Gedeckelt: ein Block kann nicht mehr als voll sein, und ein Balken,
      // der ueber den Rand laeuft, sieht nach einem Fehler aus.
      fuell: da(b.gewicht) ? Math.min(100, b.gewicht / gewicht * 100) : null,
      zeit: da(b.zeit) ? b.zeit : null,
      unten: zahl(b.hoehe), unten2: null,
      href: ZIEL ? ZIEL + b.hoehe : ""
    };
  }

  // Ein kuenftiger Block, wie mempoolseite.projektion ihn liefert. Wieviele
  // Transaktionen darin stecken, weiss das Gebuehrenhistogramm NICHT - es
  // zaehlt virtuelle Bytes, keine Transaktionen. An dieser Stelle steht
  // deshalb die Fuellung und keine erfundene Anzahl.
  function ausProj(p) {
    var f = da(p.fuellung_p) ? Math.max(0, Math.min(100, p.fuellung_p)) : null;
    return {
      art: "proj", schluessel: "p" + p.nr,
      rate: p.median,
      gross: da(p.median) ? t("rate", { n: geb(p.median) }) : STRICH,
      min: p.min, max: p.max,
      drei: f === null ? STRICH : t("voll", { p: zahl(f) }),
      mb: da(p.vsize) ? t("mb", { n: zahl(p.vsize / 1e6, 2) }) : STRICH,
      fuell: f,
      zeit: null,
      unten: p.nr === 1 ? t("naechster") : t("eta", { n: zahl(p.minuten) }),
      unten2: null, href: ""
    };
  }

  // Der Ersatz, wenn es keine Projektion gibt: die GANZE Warteschlange als
  // eine gestrichelte Kachel. Alle Zahlen stammen aus /api/kette, keine ist
  // gerechnet - die grosse Zahl ist ausdruecklich der Eintrittspreis, nicht
  // ein Median, deshalb traegt sie einen eigenen Text ("ab n sat/vB").
  function ausWarte(m) {
    var s = (m.schaetzung || [])[0];
    var e = s && da(s.satvb) ? s.satvb : m.min_gebuehr;
    return {
      art: "warte", schluessel: "w",
      rate: e,
      gross: da(e) ? t("eintritt", { n: geb(e) }) : STRICH,
      min: null, max: null,
      drei: !da(m.anzahl) ? STRICH
        : (m.anzahl === 0 ? t("leer") : t("warten", { n: zahl(m.anzahl) })),
      mb: da(m.bytes) ? t("mb", { n: zahl(m.bytes / 1e6, 1) }) : STRICH,
      fuell: da(m.fuellung) ? m.fuellung * 100 : null,
      zeit: null,
      unten: t("warteschlange"),
      unten2: da(m.blockaequivalent)
        ? t("rueckstau", { n: zahl(m.blockaequivalent, 1) }) : "",
      href: ""
    };
  }

  // ------------------------------------------------------------- Zeichnen
  function fuelle(el, o) {
    el.className = "bb-w bb-w-" + o.art;
    el.setAttribute("data-geb", stufe(o.rate));
    if (o.href) { el.setAttribute("href", o.href); } else { el.removeAttribute("href"); }
    setz(el, ".bb-rate", o.gross);
    setz(el, ".bb-spanne", (da(o.min) && da(o.max))
      ? t("spanne", { min: geb(o.min), max: geb(o.max) }) : "");
    setz(el, ".bb-txs", o.drei);
    setz(el, ".bb-mb", o.mb);
    setz(el, ".bb-hoehe", o.unten);

    // Nur bestaetigte Bloecke haben eine Zeit, die im Browser weiterlaeuft.
    var a = el.querySelector(".bb-alter");
    if (a) {
      if (o.zeit === null) {
        a.removeAttribute("data-zeit");
        a.textContent = o.unten2 || "";
      } else {
        a.setAttribute("data-zeit", o.zeit);
        a.textContent = alter(jetzt() - o.zeit);
      }
    }
    // Die Fuellung steht als Variable am WUERFEL: daran haengen Vorderseite,
    // Deckel und Seite gleichzeitig - drei Flaechen, eine Zahl. Frueher setzte
    // das JavaScript die Breite eines Streifens am unteren Rand; die drei
    // Flaechen wussten nichts voneinander, und ein halb gefuellter Block sah
    // aus wie ein voller.
    var w = el.querySelector(".bb-wuerfel");
    if (w) {
      var p = (o.fuell === null || isNaN(o.fuell)) ? 100
            : Math.max(0, Math.min(100, o.fuell));
      // Fast voll wie voll zeichnen: eine sichtbare Fuge bei 99,7 % Fuellung
      // waere ein Darstellungsfehler, kein Befund.
      if (p >= 98) { p = 100; }
      w.style.setProperty("--fuell", p.toFixed(1));
    }
  }

  function mach(o) {
    var el = muster.content.firstElementChild.cloneNode(true);
    fuelle(el, o);
    return el;
  }

  // Eine ganze Gruppe stur nach Position abgleichen. Fuer die kuenftigen
  // Bloecke richtig: sie haben keine Kennung, die zwei Abfragen ueberlebt -
  // "Block 3 von jetzt" ist in zehn Minuten ein anderer.
  function stelle(gruppe, liste) {
    while (gruppe.children.length > liste.length) {
      gruppe.removeChild(gruppe.lastChild);
    }
    for (var i = 0; i < liste.length; i++) {
      if (gruppe.children[i]) { fuelle(gruppe.children[i], liste[i]); }
      else { gruppe.appendChild(mach(liste[i])); }
    }
  }

  function zeichneMempool(p, m) {
    var liste = [], i;
    if (p && p.bloecke && p.bloecke.length) {
      // Rueckwaerts: der naechste Block (nr 1) steht ganz RECHTS, direkt an
      // der Trennung - dort verlaesst er gleich die Warteschlange.
      for (i = p.bloecke.length - 1; i >= 0; i--) { liste.push(ausProj(p.bloecke[i])); }
    } else if (m) {
      liste.push(ausWarte(m));
    }
    stelle(gMem, liste);
  }

  function austragen(hoehe) {
    var el = kacheln[hoehe];
    delete kacheln[hoehe];
    if (!el) { return; }
    if (ruhig()) { el.remove(); return; }
    el.classList.add("bb-raus");
    setTimeout(function () { el.remove(); }, 600);
  }

  function zeichneBloecke(d) {
    // /api/kette liefert AELTESTER ZUERST. Im Band steht der neueste links,
    // an der Trennung - also einmal umdrehen und danach nie wieder.
    var neu = d.bloecke.slice().reverse();
    var hoehen = neu.map(function (b) { return b.hoehe; });
    var frisch = hoehen.filter(function (h) { return bekannt.indexOf(h) < 0; });
    var i, el;

    // Erster Aufbau, Reorganisation oder lange Pause: ohne Bewegung neu
    // setzen. Zwoelf einschwebende Bloecke waeren Krach, kein Hinweis.
    if (!bekannt.length || frisch.length > 2 || ruhig()) {
      gBlk.textContent = "";
      kacheln = {};
      for (i = 0; i < neu.length; i++) {
        el = mach(ausBlock(neu[i]));
        kacheln[neu[i].hoehe] = el;
        gBlk.appendChild(el);
      }
    } else {
      // Die frischen Hoehen stehen am Anfang der Liste. Vom aeltesten frischen
      // zum neuesten jeweils ganz vorn einfuegen - am Ende steht der neueste
      // links, ohne dass die Reihe neu gezeichnet werden muesste.
      for (i = frisch.length - 1; i >= 0; i--) {
        var b = null, j;
        for (j = 0; j < neu.length; j++) {
          if (neu[j].hoehe === frisch[i]) { b = neu[j]; }
        }
        if (!b) { continue; }
        el = mach(ausBlock(b));
        kacheln[b.hoehe] = el;
        gBlk.insertBefore(el, gBlk.firstChild);
        el.classList.add("bb-neu");
        (function (x) {
          setTimeout(function () { x.classList.remove("bb-neu"); }, 1500);
        }(el));
      }
      // Die uebrigen nur nachziehen: "billigster/teuerster" wandert mit dem
      // Fenster, und die Gebuehren eines Blocks stehen erst fest, wenn
      // getblockstats beim zweiten Anlauf durchkommt.
      for (i = 0; i < neu.length; i++) {
        if (kacheln[neu[i].hoehe] && frisch.indexOf(neu[i].hoehe) < 0) {
          fuelle(kacheln[neu[i].hoehe], ausBlock(neu[i]));
        }
      }
      for (i = 0; i < bekannt.length; i++) {
        if (hoehen.indexOf(bekannt[i]) < 0) { austragen(bekannt[i]); }
      }
    }
    bekannt = hoehen;
  }

  // Die Trennung ist der Punkt, auf den es ankommt. Sie steht bei gut einem
  // Drittel der Breite, damit links die naechsten und rechts die letzten
  // Bloecke zu sehen sind. Wer selbst gerollt hat, wird nicht zurueckgerissen.
  function ausrichten() {
    if (!trenner) { return; }
    if (anker >= 0 && Math.abs(rahmen.scrollLeft - anker) > 24) { return; }
    rahmen.scrollLeft = Math.max(0, trenner.offsetLeft - rahmen.clientWidth * 0.34);
    anker = rahmen.scrollLeft;
  }

  function melde(text) {
    if (lage) { lage.textContent = text || ""; }
  }

  // Das Alter laeuft im Browser weiter, ohne dass etwas nachgeladen wird.
  function tick() {
    var n = jetzt();
    var alle = gBlk.querySelectorAll("[data-zeit]");
    for (var i = 0; i < alle.length; i++) {
      var s = parseInt(alle[i].getAttribute("data-zeit"), 10);
      alle[i].textContent = isNaN(s) ? STRICH : alter(n - s);
    }
  }

  function zeichne(d, pd) {
    if (da(d.blockgewicht)) { gewicht = d.blockgewicht; }
    // Geht die Uhr im Wohnzimmer zwei Minuten vor, stuende hier sonst ein
    // Blockalter von "-2 Min." - das sieht nach einem Fehler aus.
    if (da(d.jetzt)) { versatz = d.jetzt - Date.now() / 1e3; }
    wurzel.classList.toggle("bb-weg", !d.erreichbar);

    if (d.bloecke && d.bloecke.length) { zeichneBloecke(d); }
    var p = (pd && pd.projektion) ? pd.projektion : null;
    zeichneMempool(p, d.mempool);

    // Unter dem Band steht, was NICHT mehr in die Reihe passt. Dieselbe
    // Unterscheidung wie auf der Mempool-Seite: liegt der Schwanz unter der
    // Weiterleitungsgrenze, ist die Grenze die Aussage, sonst sein hoechster
    // Satz.
    var satz = "";
    if (!d.erreichbar) { satz = t("weg"); }
    else if (p && p.rest) {
      satz = p.rest.unter_boden
        ? t("rest", { b: zahl(p.rest.bloecke), n: geb(p.boden) })
        : t("rest_ueber", { b: zahl(p.rest.bloecke), n: geb(p.rest.hoechster) });
    }
    melde(satz);
    ausrichten();
  }

  // ------------------------------------------------------------- Abholen
  function ladeJson(u) {
    return fetch(u, { cache: "no-store" }).then(function (a) {
      return a.ok ? a.json() : Promise.reject(a.status);
    });
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
    // Die Projektion darf einzeln ausfallen, ohne das Band mitzureissen.
    // Nach drei Fehlschlaegen wird sie gar nicht mehr gefragt: gibt es den
    // Endpunkt nicht, waeren das sonst zwoelf 404 je Minute und Fenster.
    var zweite = PROJ ? ladeJson(PROJ)["catch"](function () {
      projFehl++;
      if (projFehl >= 3) { PROJ = ""; }
      return null;
    }) : Promise.resolve(null);

    Promise.all([ladeJson(QUELLE), zweite]).then(function (a) {
      laeuft = false; schlecht = 0;
      if (a[1]) { projFehl = 0; }
      wurzel.classList.remove("bb-weg");
      zeichne(a[0], a[1]);
      planen(TAKT);
    })["catch"](function () {
      laeuft = false; schlecht++;
      // Erst der zweite Fehlschlag zaehlt: ein Aussetzer ist kein Ausfall.
      if (schlecht > 1) { wurzel.classList.add("bb-weg"); melde(t("weg")); }
      planen(Math.min(60000, TAKT * Math.pow(2, schlecht)));
    });
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) { hole(); }
  });
  window.addEventListener("resize", ausrichten);

  setInterval(tick, 1000);
  hole();
}());
