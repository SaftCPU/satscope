// Bewusst winzig: Mempool laedt 4,6 MB JavaScript fuer acht Bloecke.
// Hier gibt es kein Framework und keinen Bauschritt.
(function () {
  "use strict";

  // Die Sprache steckt im html-Element - dieselbe Quelle wie serverseitig,
  // damit Zahlen auf beiden Seiten gleich aussehen.
  var SPRACHE = document.documentElement.lang === "de" ? "de-DE" : "en-US";
  var DEUTSCH = SPRACHE === "de-DE";

  function dauer(sek) {
    if (sek === null || isNaN(sek)) return "\u2013";
    if (sek < 0) sek = 0;
    var min = Math.floor(sek / 60);
    if (min < 1) return DEUTSCH ? "gerade eben" : "just now";
    if (min < 60) return DEUTSCH ? "vor " + min + " Min." : min + " min ago";
    var std = Math.floor(min / 60), rest = min % 60;
    var s = std + (DEUTSCH ? " Std." : " h") + (rest ? " " + rest + (DEUTSCH ? " Min." : " min") : "");
    return DEUTSCH ? "vor " + s : s + " ago";
  }

  // Das Blockalter laeuft weiter, ohne die Seite neu zu laden.
  function tick() {
    var jetzt = Math.floor(Date.now() / 1000);
    document.querySelectorAll("[data-alter-seit]").forEach(function (e) {
      var seit = parseInt(e.getAttribute("data-alter-seit"), 10);
      if (seit) e.textContent = dauer(jetzt - seit);
    });
  }

  tick();
  setInterval(tick, 1000);
})();
