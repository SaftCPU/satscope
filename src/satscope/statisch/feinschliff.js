/* Feinschliff: Themenwahl, Tastenbedienung, Kopieren, Ladezustaende.

   Im selben Geist wie satscope.js: ein IIFE, kein Framework, kein Bauschritt,
   keine Abhaengigkeit. Die Datei laeuft mit "defer", das DOM steht also.

   REGEL, die hier durchgehalten wird: dieses Skript fuehrt KEINE eigenen
   Saetze. Jeder sichtbare Text kommt aus dem Textkatalog und steht in den
   data-Attributen von #satscope-texte (siehe teile/kopfleiste.html). Ein
   zweiter Textvorrat im JavaScript waere der sichere Weg zu englischen
   Brocken in der deutschen Oberflaeche. */
(function () {
  "use strict";

  var wurzel = document.documentElement;
  var SPEICHER = "satscope-thema";
  var THEMEN = ["light", "dark", "system"];

  /* Falls der Schnipsel im <head> fehlt oder ausfaellt: die Klasse hier
     nachziehen. Sonst blieben alle "nur-mit-js"-Bedienelemente fuer immer
     unsichtbar - lieber ein kurzes Aufblitzen des falschen Themas als eine
     Kopfleiste ohne Knoepfe. */
  wurzel.classList.add("js");

  // ------------------------------------------------------------- Textvorrat
  var TEXTE = (function () {
    var q = document.getElementById("satscope-texte");
    // Vorsatz "data-text-", damit der Textvorrat nicht selbst in das
    // Suchmuster [data-kopieren] der Kopierknoepfe faellt.
    function lies(name) {
      return (q && q.getAttribute("data-text-" + name)) || "";
    }
    return {
      kopieren: lies("kopieren"),
      kopiert: lies("kopiert"),
      fehler: lies("fehler"),
      wertFehlt: lies("wert-fehlt"),
      wertLaedt: lies("wert-laedt")
    };
  })();

  var melder = document.getElementById("satscope-melder");

  /* Rueckmeldung fuer Vorleseprogramme. Wer den Haken am Kopierknopf nicht
     SIEHT, erfaehrt sonst nie, ob es geklappt hat. Der Umweg ueber ein
     kurzes Leeren erzwingt die Ansage auch dann, wenn zweimal derselbe Text
     gemeldet wird - sonst schweigt die Live-Region beim zweiten Mal. */
  function melde(text) {
    if (!melder || !text) return;
    melder.textContent = "";
    setTimeout(function () { melder.textContent = text; }, 40);
  }

  // ------------------------------------------------------------- Themenwahl
  /* "system" heisst: KEIN data-theme setzen. Dann greift die Medienabfrage
     prefers-color-scheme aus satscope.css - dieselbe Mechanik, die ohne
     JavaScript auch schon gilt. Ein eigenes data-theme="system" waere ein
     dritter Zustand, den das CSS gar nicht kennt. */
  function themaLesen() {
    try {
      var w = localStorage.getItem(SPEICHER);
      return THEMEN.indexOf(w) >= 0 ? w : "system";
    } catch (e) {
      // Privater Modus mit gesperrtem Speicher wirft hier. Kein Grund,
      // die Seite zu verlieren - dann eben ohne Erinnerung.
      return "system";
    }
  }

  function themaAnwenden(wahl, merken) {
    if (wahl === "light" || wahl === "dark") wurzel.setAttribute("data-theme", wahl);
    else wurzel.removeAttribute("data-theme");

    document.querySelectorAll(".thema-knopf").forEach(function (k) {
      k.setAttribute("aria-pressed", k.getAttribute("data-thema") === wahl ? "true" : "false");
    });

    if (merken) {
      try { localStorage.setItem(SPEICHER, wahl); } catch (e) { /* s. oben */ }
    }
  }

  function themaWeiter() {
    var jetzt = themaLesen();
    var i = THEMEN.indexOf(jetzt);
    var neu = THEMEN[(i + 1) % THEMEN.length];
    themaAnwenden(neu, true);
    var knopf = document.querySelector('.thema-knopf[data-thema="' + neu + '"]');
    if (knopf) melde(knopf.getAttribute("title") || "");
  }

  document.querySelectorAll(".thema-knopf").forEach(function (k) {
    k.addEventListener("click", function () {
      themaAnwenden(k.getAttribute("data-thema"), true);
    });
  });

  /* Zwei offene Tabs derselben App sollen nicht verschieden aussehen.
     Das storage-Ereignis kommt nur in den ANDEREN Tabs an - deshalb hier
     nicht erneut speichern, das gaebe eine Schleife. */
  window.addEventListener("storage", function (e) {
    if (e.key === SPEICHER) themaAnwenden(themaLesen(), false);
  });

  themaAnwenden(themaLesen(), false);

  // ------------------------------------------------------------- Kopieren
  /* ⚠️ Umbrel liefert seine Apps ueber http://umbrel.local:PORT aus - das ist
     KEIN sicherer Kontext. navigator.clipboard existiert dort schlicht nicht.
     Ohne den zweiten Weg koennte die Mehrheit der Nutzer nicht kopieren; das
     veraltete execCommand ist hier also kein Schlendrian, sondern der einzige
     Weg, der auf dem echten Geraet funktioniert. */
  function inZwischenablage(text, fertig) {
    var api = navigator.clipboard;
    if (api && api.writeText && window.isSecureContext) {
      api.writeText(text).then(
        function () { fertig(true); },
        function () { fertig(ueberFeld(text)); }
      );
      return;
    }
    fertig(ueberFeld(text));
  }

  function ueberFeld(text) {
    try {
      var feld = document.createElement("textarea");
      feld.value = text;
      feld.setAttribute("readonly", "");
      // Ausserhalb des Bildes, aber NICHT display:none - eine unsichtbare
      // Auswahl laesst sich nicht kopieren.
      feld.style.cssText = "position:fixed;top:0;left:-9999px;opacity:0";
      document.body.appendChild(feld);
      feld.select();
      feld.setSelectionRange(0, text.length);
      var ok = document.execCommand("copy");
      document.body.removeChild(feld);
      return ok;
    } catch (e) {
      return false;
    }
  }

  var HAKEN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" ' +
    'aria-hidden="true" focusable="false"><path d="M4.5 12.5l5 5 10-11"/></svg>';
  var BLATT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" ' +
    'aria-hidden="true" focusable="false">' +
    '<rect x="9" y="9" width="11" height="11" rx="2"/>' +
    '<path d="M15 5.5A1.5 1.5 0 0 0 13.5 4h-7A2.5 2.5 0 0 0 4 6.5v7A1.5 1.5 0 0 0 5.5 15"/></svg>';

  function rueckmeldung(knopf, ok) {
    knopf.innerHTML = ok ? HAKEN : BLATT;
    knopf.classList.add(ok ? "geschafft" : "misslungen");
    knopf.setAttribute("data-meldung", ok ? TEXTE.kopiert : TEXTE.fehler);
    melde(ok ? TEXTE.kopiert : TEXTE.fehler);
    setTimeout(function () {
      knopf.innerHTML = BLATT;
      knopf.classList.remove("geschafft", "misslungen");
      knopf.removeAttribute("data-meldung");
    }, 1600);
  }

  function kopiere(wert, knopf) {
    if (!wert) return;
    inZwischenablage(wert, function (ok) {
      if (knopf) rueckmeldung(knopf, ok);
      else melde(ok ? TEXTE.kopiert : TEXTE.fehler);
    });
  }

  /* Kopierknoepfe anhaengen. Zwei Quellen:
       [data-kopieren]  - der volle Wert steht im Attribut (Pflicht dort, wo
                          die Anzeige gekuerzt ist: ein abgeschnittener Hash
                          in der Zwischenablage ist schlimmer als keiner)
       .kennung         - die volle Adresse steht sichtbar da
     Ein echter <button>, kein klickbares span: Tastatur, Vorlesetechnik und
     Fokusrahmen kommen damit geschenkt. */
  function knoepfeSetzen(bereich) {
    (bereich || document).querySelectorAll("[data-kopieren],.kennung").forEach(function (e) {
      if (e.getAttribute("data-kopieren-fertig")) return;
      var wert = e.getAttribute("data-kopieren") || e.textContent.trim();
      if (!wert) return;
      // Den Wert festhalten, BEVOR der Knopf im Element steht - sonst zaehlt
      // dessen Beschriftung beim naechsten Auslesen mit.
      e.setAttribute("data-kopieren", wert);
      e.setAttribute("data-kopieren-fertig", "1");

      var knopf = document.createElement("button");
      knopf.type = "button";
      knopf.className = "kopieren";
      knopf.innerHTML = BLATT;
      knopf.setAttribute("aria-label", TEXTE.kopieren);
      knopf.setAttribute("title", TEXTE.kopieren);
      knopf.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        kopiere(wert, knopf);
      });
      e.appendChild(knopf);
    });
  }
  knoepfeSetzen(document);

  // ------------------------------------------------------------- Fehlende Werte
  /* Ein Gedankenstrich ist fuer das Auge eindeutig und fuer ein
     Vorleseprogramm nichts: es liest ihn meist gar nicht vor, die Zeile klingt
     dann leer. Der Strich bleibt stehen, daneben kommt der Grund in Worten. */
  var STRICH = "–";
  function fehlendeMarkieren(bereich) {
    if (!TEXTE.wertFehlt) return;
    (bereich || document).querySelectorAll(".w,.wert").forEach(function (e) {
      if (e.classList.contains("fehlt") || e.classList.contains("laedt")) return;
      if (e.textContent.trim() !== STRICH) return;
      e.classList.add("fehlt");
      e.setAttribute("title", TEXTE.wertFehlt);
      var wort = document.createElement("span");
      wort.className = "nur-lesbar";
      wort.textContent = TEXTE.wertFehlt;
      e.appendChild(wort);
    });
  }
  fehlendeMarkieren(document);

  // ------------------------------------------------------------- Ladezustand
  /* Der Unterschied, auf den es ankommt:
       .laedt  = der Wert ist unterwegs    -> ruhiges Flimmern, feste Groesse
       .fehlt  = der Wert kam nicht        -> Strich, endgueltig
     Ein Platzhalter, der ewig flimmert, waere ein Versprechen, das die App
     nicht halten kann. Deshalb ist das Flimmern IMMER befristet: kommt binnen
     zwoelf Sekunden nichts, wird daraus ein Strich. Zwoelf Sekunden, weil das
     RPC-Tor bei fuenf abbricht und der Electrum-Weg bei fuenfundzwanzig - ein
     Wert dazwischen faellt nicht vor dem Knoten um, aber lange vor dem
     Geduldsfaden des Nutzers. */
  var FRIST = 12000;

  function laden(e) {
    if (!e) return;
    e.classList.add("laedt");
    e.classList.remove("fehlt");
    if (TEXTE.wertLaedt) e.setAttribute("aria-label", TEXTE.wertLaedt);
    e.setAttribute("aria-busy", "true");
    clearTimeout(e._frist);
    e._frist = setTimeout(function () {
      if (!e.classList.contains("laedt")) return;
      fertig(e);
      if (!e.textContent.trim()) e.textContent = STRICH;
      fehlendeMarkieren(e.parentNode || document);
    }, FRIST);
  }

  function fertig(e) {
    if (!e || !e.classList.contains("laedt")) return;
    clearTimeout(e._frist);
    e.classList.remove("laedt");
    e.removeAttribute("aria-busy");
    if (TEXTE.wertLaedt && e.getAttribute("aria-label") === TEXTE.wertLaedt) {
      e.removeAttribute("aria-label");
    }
  }

  /* Wer spaeter per WebSocket Werte nachliefert, soll nicht auch noch daran
     denken muessen, das Flimmern abzuschalten. Sobald echter Text im Element
     steht, ist der Ladezustand vorbei. */
  if (window.MutationObserver) {
    new MutationObserver(function (aenderungen) {
      aenderungen.forEach(function (a) {
        var e = a.target.nodeType === 1 ? a.target : a.target.parentNode;
        while (e && e !== document.body) {
          if (e.classList && e.classList.contains("laedt")) {
            if (e.textContent.trim()) fertig(e);
            return;
          }
          e = e.parentNode;
        }
      });
    }).observe(document.body, { childList: true, characterData: true, subtree: true });
  }

  document.querySelectorAll(".laedt").forEach(laden);

  // ------------------------------------------------------------- Tastatur
  function suchfeld() {
    var felder = document.querySelectorAll('input[name="q"]');
    for (var i = 0; i < felder.length; i++) {
      // Das Kopf-Suchfeld ist auf Seiten mit grosser Suche ausgeblendet;
      // fokussieren liesse sich das nicht, tippen erst recht nicht.
      if (felder[i].offsetParent !== null) return felder[i];
    }
    return felder[0] || null;
  }

  function tipptGerade(ziel) {
    if (!ziel) return false;
    var name = (ziel.tagName || "").toLowerCase();
    return name === "input" || name === "textarea" || name === "select" ||
           ziel.isContentEditable === true;
  }

  var hilfe = document.querySelector(".hilfe");

  function hilfeZeigen(an) {
    if (!hilfe) return;
    hilfe.open = an;
    if (an) {
      var s = hilfe.querySelector("summary");
      if (s) s.focus();
    }
  }

  /* Die Sprungziele stehen in der Kuerzel-Liste der Kopfleiste, nicht hier.
     So kann die Anzeige nicht von der Wirkung abweichen - der klassische
     Fehler bei Hilfetexten, die irgendwann nur noch Folklore sind. */
  var KETTEN = {};
  document.querySelectorAll(".kuerzel li[data-kette][data-ziel]").forEach(function (li) {
    var ziel = li.getAttribute("data-ziel");
    if (ziel) KETTEN[li.getAttribute("data-kette")] = ziel;
  });

  var offen = null;      // erste Taste einer Kette
  var offenBis = 0;

  document.addEventListener("keydown", function (e) {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    /* Die Umschalttaste meldet sich mit einem eigenen keydown, BEVOR das
       Zeichen kommt. Ohne diese Zeile bricht sie jede angefangene Kette ab -
       und "?" ist auf beiden Tastaturen ein Umschalt-Zeichen. */
    if (e.key === "Shift") return;

    // Esc wirkt AUCH im Eingabefeld - das ist ja gerade sein Zweck.
    if (e.key === "Escape") {
      if (hilfe && hilfe.open) { hilfeZeigen(false); e.preventDefault(); return; }
      if (tipptGerade(document.activeElement)) {
        document.activeElement.blur();
        e.preventDefault();
      }
      return;
    }

    // Alles Weitere darf beim Tippen nicht dazwischenfunken.
    if (tipptGerade(e.target)) return;

    if (e.key === "/") {
      var f = suchfeld();
      if (f) {
        e.preventDefault();          // sonst landet der Schraegstrich im Feld
        f.focus();
        f.select();
      }
      return;
    }

    if (e.key === "?") { hilfeZeigen(!(hilfe && hilfe.open)); e.preventDefault(); return; }

    var taste = e.key.toLowerCase();

    // Zweite Taste einer angefangenen Kette?
    if (offen && Date.now() < offenBis) {
      var ziel = KETTEN[offen + " " + taste];
      offen = null;
      if (ziel) { e.preventDefault(); window.location.href = ziel; return; }
      return;
    }
    offen = null;

    if (taste === "g") {
      // Eine Sekunde: lang genug zum Nachdenken, kurz genug, dass ein
      // spaeteres "b" nicht ploetzlich navigiert.
      offen = "g";
      offenBis = Date.now() + 1000;
      e.preventDefault();
      return;
    }

    if (taste === "t") { themaWeiter(); e.preventDefault(); return; }

    if (taste === "c") {
      // Kopiert die Hauptkennung der Seite - auf der Adressseite die Adresse.
      // Zwei getrennte Abfragen, KEINE Auswahlliste: eine Liste waehlt das im
      // Dokument zuerst stehende Element, nicht das zuerst genannte Muster -
      // die Kennung haette also gegen irgendein frueheres data-kopieren
      // verloren.
      var haupt = document.querySelector(".kennung[data-kopieren]") ||
                  document.querySelector("[data-kopieren]");
      if (haupt) {
        e.preventDefault();
        kopiere(haupt.getAttribute("data-kopieren"), haupt.querySelector(".kopieren"));
      }
      return;
    }
  });

  // Klick daneben schliesst die Kuerzel-Uebersicht. Ohne das bleibt sie
  // offen stehen und verdeckt den Inhalt.
  document.addEventListener("click", function (e) {
    if (hilfe && hilfe.open && !hilfe.contains(e.target)) hilfe.open = false;
  });

  // ------------------------------------------------------------- Sprunglink
  /* Der Sprunglink in der Kopfleiste zeigt auf #inhalt. Steht das Ziel nicht
     in der Vorlage, wird es hier nachgetragen - ein Sprunglink ins Leere ist
     schlimmer als keiner. */
  (function () {
    var m = document.querySelector("main");
    if (m && !m.id) m.id = "inhalt";
    // Ohne tabindex springt der Fokus in manchen Browsern zwar sichtbar,
    // bleibt aber im Kopfbereich haengen; die naechste Tabulatortaste
    // fuehrte dann zurueck in die Leiste.
    if (m && !m.hasAttribute("tabindex")) m.setAttribute("tabindex", "-1");
  })();

  // ------------------------------------------------------------- fuer andere Bausteine
  /* Eine kleine, ausdrueckliche Schnittstelle statt verstreuter Kopien.
     Satscope.zahl spiegelt die Regeln aus sprache.py: deutsch 1.234,56 gegen
     englisch 1,234.56 - wer im Browser mit toFixed formatiert, baut die
     Diskrepanz ein, die zweisprachige Oberflaechen zuverlaessig entstellt. */
  var GEBIET = document.documentElement.lang === "de" ? "de-DE" : "en-US";
  window.Satscope = {
    sprache: document.documentElement.lang === "de" ? "de" : "en",
    gebiet: GEBIET,
    zahl: function (wert, stellen) {
      if (wert === null || wert === undefined || isNaN(wert)) return STRICH;
      return Number(wert).toLocaleString(GEBIET, {
        minimumFractionDigits: stellen || 0,
        maximumFractionDigits: stellen || 0
      });
    },
    laden: laden,
    fertig: fertig,
    melde: melde,
    kopiere: kopiere,
    knoepfeSetzen: knoepfeSetzen,
    fehlendeMarkieren: fehlendeMarkieren,
    thema: themaLesen
  };
})();
