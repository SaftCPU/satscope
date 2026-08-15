// Die spielerischen Kennzahlen im Browser: der Ticker laeuft, die
// Ueberraschungszeile blaettert, und einmal je Minute wird nachgefragt, ob
// ein neuer Block da ist.
//
// Kein Framework, kein Bauschritt, kein einziger Aufruf nach draussen - die
// einzige Adresse, die hier vorkommt, ist die eigene.
//
// Grundsaetze:
// * Zahlen werden mit toLocaleString gesetzt. Die Sprache steht im
//   html-Element, also derselbe Ursprung wie serverseitig - sonst stuende
//   1.234,56 neben 1,234.56 auf einer Seite.
// * Saetze werden NIE hier zusammengebaut. Was Woerter enthaelt, kommt
//   fertig aus dem Katalog: entweder schon im Text oder als Vorlage in einem
//   data-Attribut, in der nur noch {n} ersetzt wird.
// * Geschaetzte Werte tragen ihr "≈" mit. Wer eine Schaetzung wie eine
//   Messung aussehen laesst, luegt.
(function () {
  "use strict";

  var wurzel = document.querySelector("[data-spiel-tick]");
  var SPRACHE = document.documentElement.lang === "de" ? "de-DE" : "en-US";
  var RUHIG = window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Ein Block dauert im Mittel zehn Minuten; danach ist er ueberfaellig.
  var TAKT = 600;
  // Einmal je Minute nachfragen. Oefter waere reine Last auf einem Knoten,
  // auf dem echtes Geld liegt - der Gewinn waere hoechstens ein paar
  // Sekunden frueher zu wissen, dass ein Block da ist.
  var PULS_MS = 60000;
  var HALBIERUNG_ALLE = 210000;
  var PERIODE = 2016;

  function zahl(wert, stellen) {
    if (wert === null || wert === undefined || isNaN(wert)) return "–";
    return Number(wert).toLocaleString(SPRACHE, {
      minimumFractionDigits: stellen || 0,
      maximumFractionDigits: stellen || 0
    });
  }

  function stoppuhr(sek) {
    if (sek === null || isNaN(sek)) return "–";
    if (sek < 0) sek = 0;                   // eine schiefe Rechneruhr
    sek = Math.floor(sek);
    var std = Math.floor(sek / 3600);
    var min = Math.floor((sek % 3600) / 60);
    var s = sek % 60;
    function zwei(n) { return (n < 10 ? "0" : "") + n; }
    return std ? std + ":" + zwei(min) + ":" + zwei(s) : zwei(min) + ":" + zwei(s);
  }

  function feld(name) {
    return wurzel ? wurzel.querySelector('[data-spiel="' + name + '"]') : null;
  }

  function ganz(name) {
    return document.querySelector('[data-spiel="' + name + '"]');
  }

  function lies(el, name) {
    var w = el && el.getAttribute(name);
    if (w === null || w === undefined || w === "") return null;
    var n = Number(w);
    return isNaN(n) ? null : n;
  }

  // --------------------------------------------------------- Datumsangaben
  // Serverseitig steht nur "≈ 607 Tage"; das genaue Datum haengt an der
  // Zeitzone des Lesers und gehoert deshalb hierher.
  function datenSetzen() {
    document.querySelectorAll("[data-spiel-datum]").forEach(function (el) {
      var ts = lies(el, "data-spiel-datum");
      if (!ts) return;
      el.textContent = new Date(ts * 1000).toLocaleDateString(SPRACHE, {
        year: "numeric", month: "short", day: "numeric"
      });
    });
  }

  // --------------------------------------------------------- Ueberraschungen
  function ueberraschungen() {
    var kasten = document.querySelector("[data-spiel-liste]");
    if (!kasten) return;
    var zeilen;
    try {
      zeilen = JSON.parse(kasten.getAttribute("data-spiel-liste"));
    } catch (e) {
      return;                               // lieber die eine Zeile als nichts
    }
    if (!Array.isArray(zeilen) || zeilen.length < 2) return;

    var text = kasten.querySelector('[data-spiel="ueberraschung"]');
    var knopf = kasten.querySelector('[data-spiel="noch"]');
    var bei = 0;
    var vonHand = false;

    function weiter() {
      bei = (bei + 1) % zeilen.length;
      text.textContent = zeilen[bei];
      // Klasse neu setzen, damit die Einblendung erneut anlaeuft.
      text.classList.remove("ist-neu");
      void text.offsetWidth;
      text.classList.add("ist-neu");
    }

    if (knopf) {
      knopf.hidden = false;                 // erst jetzt hat er etwas zu tun
      knopf.addEventListener("click", function () {
        vonHand = true;
        weiter();
      });
    }

    // Von allein weiterblaettern nur, wenn niemand um Ruhe gebeten hat und
    // solange der Leser nicht selbst uebernommen hat.
    if (!RUHIG) {
      setInterval(function () {
        if (!vonHand && !document.hidden) weiter();
      }, 15000);
    }
  }

  // --------------------------------------------------------- Der Ticker
  var stand = null;

  function standLesen() {
    if (!wurzel) return null;
    return {
      blockZeit: lies(wurzel, "data-spiel-block-zeit"),
      hoehe: lies(wurzel, "data-spiel-hoehe"),
      txRate: lies(wurzel, "data-spiel-txrate"),
      heuteSat: lies(wurzel, "data-spiel-heute-sat"),
      heuteBloecke: lies(wurzel, "data-spiel-heute-bloecke"),
      subvention: lies(wurzel, "data-spiel-subvention"),
      tag: new Date().getDate()
    };
  }

  function tick() {
    if (!stand || stand.blockZeit === null) return;
    var seit = Math.floor(Date.now() / 1000) - stand.blockZeit;

    var uhr = feld("seit");
    if (uhr) uhr.textContent = stoppuhr(seit);

    // Die geschaetzte Zahl der Transaktionen seit dem letzten Block. Der
    // Satz nebenan nennt die Rate, mit der gerechnet wird - eine Schaetzung
    // ohne ihre Grundlage waere eine blosse Behauptung.
    var tx = feld("txseit");
    if (tx && stand.txRate) {
      tx.textContent = "≈ " + zahl(Math.max(0, seit) * stand.txRate, 0);
    }

    var warn = feld("ueberfaellig");
    if (warn) {
      var drueber = seit > TAKT;
      warn.hidden = !drueber;
      if (drueber && !warn.textContent) {
        warn.textContent = wurzel.getAttribute("data-spiel-ueberfaellig") || "";
      }
    }
  }

  // --------------------------------------------------------- Neuer Block
  function zaehlerNeuSetzen(hoehe) {
    // Reine Arithmetik aus der Hoehe - dafuer muss niemand gefragt werden.
    var restHalb = HALBIERUNG_ALLE - (hoehe % HALBIERUNG_ALLE);
    var restAnp = PERIODE - (hoehe % PERIODE);

    var a = ganz("halbierung-rest");
    if (a) a.textContent = zahl(restHalb, 0);
    var b = ganz("anpassung-rest");
    if (b) b.textContent = zahl(restAnp, 0);

    var bh = ganz("halbierung-balken");
    if (bh) bh.style.width = ((hoehe % HALBIERUNG_ALLE) * 100 / HALBIERUNG_ALLE).toFixed(2) + "%";
    var ba = ganz("anpassung-balken");
    if (ba) ba.style.width = ((hoehe % PERIODE) * 100 / PERIODE).toFixed(2) + "%";
  }

  function mempoolSetzen(bytes, anzahl) {
    if (bytes !== null) {
      var el = ganz("mempool-bloecke");
      // getmempoolinfo.bytes zaehlt virtuelle Groesse, ein Block fasst rund
      // 1.000.000 vB - die Division ist damit "wie viele Bloecke tief".
      if (el) el.textContent = zahl(bytes / 1000000, 1);
    }
    if (anzahl !== null) {
      var z = ganz("mempool-anzahl");
      // Der Satz kommt als Vorlage aus dem Katalog; hier wird nur die Zahl
      // eingesetzt. Zusammengebaut wird in dieser Datei kein Satz.
      var vorlage = z && z.getAttribute("data-spiel-vorlage");
      if (z && vorlage) z.textContent = vorlage.replace("{n}", zahl(anzahl, 0));
    }
  }

  function neuerBlock(neu) {
    var vorher = stand.hoehe;
    var dazu = (vorher === null) ? 0 : neu.hoehe - vorher;

    // Ueber Mitternacht hinweg stimmt der Tageszaehler nicht mehr, und
    // raten wollen wir nicht: dann laedt die Seite lieber neu.
    if (dazu > 0 && new Date().getDate() !== stand.tag) {
      window.location.reload();
      return;
    }

    stand.hoehe = neu.hoehe;
    stand.blockZeit = neu.blockZeit;
    if (neu.subvention !== null) stand.subvention = neu.subvention;

    if (dazu > 0 && stand.heuteBloecke !== null) {
      stand.heuteBloecke += dazu;
      var el = ganz("heute-bloecke");
      if (el) el.textContent = zahl(stand.heuteBloecke, 0);
    }
    if (dazu > 0 && stand.heuteSat !== null && stand.subvention !== null) {
      stand.heuteSat += dazu * stand.subvention;
      var btc = ganz("heute-btc");
      if (btc) btc.textContent = zahl(stand.heuteSat / 100000000, 3);
    }

    zaehlerNeuSetzen(neu.hoehe);

    if (dazu > 0 && wurzel) {
      wurzel.classList.remove("ist-neuer-block");
      void wurzel.offsetWidth;
      wurzel.classList.add("ist-neuer-block");
      marke();
      setTimeout(function () {
        wurzel.classList.remove("ist-neuer-block");
      }, 1800);
    }
  }

  // Das Woertchen "Neuer Block" kommt aus dem Katalog und haengt am
  // data-Attribut - im Skript steht kein einziges uebersetzbares Wort.
  function marke() {
    var kopf = wurzel.querySelector(".spiel-kopf");
    var wort = wurzel.getAttribute("data-spiel-neuer-block");
    if (!kopf || !wort) return;
    var alt = kopf.querySelector(".spiel-marke");
    if (alt) alt.remove();
    var m = document.createElement("span");
    m.className = "spiel-marke";
    m.textContent = wort;
    kopf.appendChild(m);
    setTimeout(function () { m.remove(); }, 12000);
  }

  // --------------------------------------------------------- Nachfragen
  var fehlschlaege = 0;

  function puls() {
    // Nur fragen, wenn jemand hinsieht. Ein Reiter im Hintergrund hat keinen
    // Grund, einen Bitcoin-Knoten zu beschaeftigen.
    if (document.hidden || fehlschlaege >= 3) return;
    fetch("/spiel.json", { headers: { "Accept": "application/json" } })
      .then(function (a) {
        if (!a.ok) throw new Error("HTTP " + a.status);
        return a.json();
      })
      .then(function (d) {
        fehlschlaege = 0;
        if (!d || typeof d.hoehe !== "number") return;
        mempoolSetzen(
          typeof d.mempool_bytes === "number" ? d.mempool_bytes : null,
          typeof d.mempool_anzahl === "number" ? d.mempool_anzahl : null);
        if (d.hoehe !== stand.hoehe) {
          neuerBlock({
            hoehe: d.hoehe,
            blockZeit: typeof d.block_zeit === "number" ? d.block_zeit : stand.blockZeit,
            subvention: typeof d.subvention_sat === "number" ? d.subvention_sat : null
          });
        }
      })
      .catch(function () {
        // Kein Alarm auf der Seite: die Zahlen von eben bleiben stehen und
        // sind weiterhin wahr, nur nicht mehr taufrisch. Nach drei
        // Fehlschlagen wird nicht weiter gefragt.
        fehlschlaege += 1;
      });
  }

  // --------------------------------------------------------- Start
  datenSetzen();
  ueberraschungen();

  if (wurzel) {
    stand = standLesen();
    tick();
    setInterval(tick, 1000);
    if (window.fetch) {
      setInterval(puls, PULS_MS);
      // Wer nach laengerer Abwesenheit zurueckkommt, soll nicht auf die
      // naechste volle Minute warten muessen.
      document.addEventListener("visibilitychange", function () {
        if (!document.hidden) puls();
      });
    }
  }
})();
