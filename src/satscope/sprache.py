"""Zweisprachigkeit: Textkatalog und sprachbewusste Formatierung.

ENTSCHEIDUNGEN (Daniel, 15.08.2026) und warum sie so umgesetzt sind:

* Englisch und Deutsch, **Englisch ist die Vorgabe**.
* Die Wahl wird **pro Browser** gespeichert. Umbrel kennt nur EINEN Login fuer
  den ganzen Knoten - Benutzerkonten innerhalb einer App gibt es nicht.
* Gespeichert wird in einem **Cookie, nicht in localStorage**. Die Seiten werden
  serverseitig gerendert; localStorage kennt der Server beim ersten Aufruf nicht,
  die Seite kaeme englisch und wuerde sichtbar umspringen. Ein Cookie wird
  mitgeschickt, der Server rendert sofort richtig.
* **Kein** Erraten ueber Accept-Language: Vorgabe ist Englisch, bis jemand waehlt.

DREI REGELN, die man nicht nachruesten kann:

1. Nur BENANNTE Platzhalter. Die Entwurfsidee sind Befunde in ganzen Saetzen,
   und dort weicht die Wortstellung ab:
       de  "Dieser Block hat {cdd} Coin-Tage vernichtet."
       en  "This block destroyed {cdd} coin days."
   Positionelle Platzhalter ({0}) waeren hier schon falsch.

2. Ein EINZIGER Formatierer fuer Zahlen. Deutsch 1.234,56 gegen englisch
   1,234.56 - vertauscht. Verstreute f"{x:,.2f}" sind der klassische Fehler.
   Bewusst KEIN locale-Modul: in schlanken Containern sind Locales oft gar
   nicht erzeugt, dann faellt es still auf C zurueck.

3. Der WebSocket schickt ROHWERTE, keine fertigen Texte - sonst muesste der
   Server die Sprache je Verbindung kennen und bei jedem Wechsel neu senden.
   Der Browser formatiert; satscope.js spiegelt die Regeln von hier.
"""
import json
import os

SPRACHEN = ("en", "de")
STANDARD = "en"
COOKIE = "satscope_lang"
COOKIE_ALTER = 365 * 24 * 3600

_KATALOG = {}


def _laden(sprache):
    if sprache not in _KATALOG:
        pfad = os.path.join(os.path.dirname(__file__), "texte", sprache + ".json")
        with open(pfad, encoding="utf-8") as f:
            _KATALOG[sprache] = json.load(f)
    return _KATALOG[sprache]


def sprache_aus_cookies(cookies):
    """Gueltige Sprache aus den Cookies, sonst die Vorgabe."""
    wahl = (cookies or {}).get(COOKIE)
    return wahl if wahl in SPRACHEN else STANDARD


def zahl(wert, stellen=0, sprache=STANDARD):
    """Zahl in der Schreibweise der jeweiligen Sprache.

    >>> zahl(1234.56, 2, "en")
    '1,234.56'
    >>> zahl(1234.56, 2, "de")
    '1.234,56'
    """
    if wert is None:
        return "\u2013"                      # Strich statt erfundener Zahl
    s = "{:,.{}f}".format(wert, stellen)     # englische Schreibweise
    if sprache == "de":
        # Tausch ueber ein Zwischenzeichen, sonst ueberschreibt der zweite
        # Ersetzungsschritt das Ergebnis des ersten.
        s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return s


class Texte:
    """Traegt Sprache, Katalog und Formatierung durch eine Anfrage."""

    def __init__(self, sprache=STANDARD):
        self.sprache = sprache if sprache in SPRACHEN else STANDARD
        self._k = _laden(self.sprache)
        self._rueckfall = _laden(STANDARD) if self.sprache != STANDARD else self._k

    def t(self, schluessel, **werte):
        """Text zum Schluessel, benannte Platzhalter eingesetzt.

        Fehlt ein Schluessel in der gewaehlten Sprache, greift Englisch; fehlt
        er auch dort, wird der Schluessel selbst sichtbar zurueckgegeben - eine
        Luecke soll auffallen, nicht stillschweigend leer bleiben.
        """
        vorlage = self._k.get(schluessel) or self._rueckfall.get(schluessel)
        if vorlage is None:
            return "!" + schluessel + "!"
        try:
            return vorlage.format(**werte)
        except KeyError as fehlt:
            return "!" + schluessel + " ohne " + str(fehlt) + "!"

    def zahl(self, wert, stellen=0):
        return zahl(wert, stellen, self.sprache)
