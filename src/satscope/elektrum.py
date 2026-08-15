"""Zugriff auf den Electrum-Server (electrs oder Fulcrum).

Umbrel stellt ihn ueber APP_ELECTRS_NODE_IP/PORT bereit. Welches der beiden
Programme dahintersteckt, ist uns gleich: Fulcrum meldet im eigenen Manifest
`implements: electrs` und wird deshalb unter denselben Variablen gefuehrt.

⚠️ ANONYMITAETSAUFLAGE (Daniels Vorgabe, gilt seit der Electrum-Statusseite):
`server.banner` und `server.version` duerfen NIEMALS angezeigt werden - ihre
Antworten nennen Software und Fassung ("Fulcrum 2.1.1"). Der Handshake ist vom
Protokoll vorgeschrieben, seine Antwort wird hier gelesen und sofort verworfen.
Keine Funktion dieses Moduls gibt sie zurueck.

Das Protokoll ist zeilenweises JSON ueber TCP - eine Bibliothek braucht es dafuer
nicht, und eine weniger ist im Container ein Angriffsweg weniger.
"""
import asyncio
import json
import os

# ⚠️ asyncios Vorgabe ist 64 KB je Zeile. Eine Adresse mit grosser Historie
# sprengt das um Groessenordnungen: die Genesis-Adresse liefert 65.311 Eintraege
# in EINER Zeile. Ohne dieses Limit endet der Aufruf in
# "Separator is not found, and chunk exceed the limit" - gemessen 15.08.2026.
ZEILENLIMIT = 64 * 1024 * 1024

# Ab hier wird eine Historie nicht mehr aufgelistet, sondern nur zusammengefasst.
# Ein Browser kaempft mit 30.000 Tabellenzeilen laenger als der Server mit der
# Abfrage - und niemand liest sie.
LISTENGRENZE = 2000


class ElektrumFehler(Exception):
    pass


def ziel():
    """(Host, Port) aus der Umgebung; (None, None), wenn nicht gesetzt."""
    return os.environ.get("ELECTRUM_HOST"), os.environ.get("ELECTRUM_PORT")


class Verbindung:
    """Kurzlebige Verbindung fuer eine Abfrage. Kein Bestand, keine Zustaende."""

    def __init__(self, host, port, zeitlimit=20.0):
        self.host, self.port, self.zeitlimit = host, port, zeitlimit
        self._leser = self._schreiber = None
        self._nummer = 0

    async def __aenter__(self):
        self._leser, self._schreiber = await asyncio.wait_for(
            asyncio.open_connection(self.host, int(self.port), limit=ZEILENLIMIT),
            timeout=self.zeitlimit)
        # Vom Protokoll vorgeschrieben. Die Antwort nennt die Software; sie wird
        # gelesen, damit die Zeile aus dem Puffer verschwindet, und weggeworfen.
        await self.frage("server.version", ["satscope", "1.4"])
        return self

    async def __aexit__(self, *_):
        if self._schreiber is not None:
            try:
                self._schreiber.close()
                await self._schreiber.wait_closed()
            except (OSError, asyncio.TimeoutError):
                pass

    async def frage(self, methode, argumente=None):
        self._nummer += 1
        self._schreiber.write((json.dumps({
            "jsonrpc": "2.0", "id": self._nummer,
            "method": methode, "params": argumente or []}) + "\n").encode())
        await self._schreiber.drain()
        zeile = await asyncio.wait_for(self._leser.readline(),
                                       timeout=self.zeitlimit)
        if not zeile:
            raise ElektrumFehler("Verbindung vorzeitig geschlossen")
        antwort = json.loads(zeile.decode())
        if antwort.get("error"):
            raise ElektrumFehler("%s: %s" % (methode, antwort["error"]))
        return antwort.get("result")


async def index_hoehe(host=None, port=None, zeitlimit=5.0):
    """Hoehe, bis zu der der Index aufgebaut ist. None bei Ausfall.

    Bewusst None statt einer Ausnahme: die Seite zeigt dann einen Strich, wie
    bei jedem anderen fehlenden Wert auch.
    """
    if host is None and port is None:
        host, port = ziel()
    if not host or not port:
        return None
    try:
        async with Verbindung(host, port, zeitlimit) as v:
            wert = await v.frage("blockchain.headers.subscribe")
        hoehe = (wert or {}).get("height")
        return int(hoehe) if isinstance(hoehe, int) else None
    except (OSError, asyncio.TimeoutError, ValueError, TypeError,
            ElektrumFehler, json.JSONDecodeError):
        return None


async def adress_uebersicht(kennung, zeitlimit=25.0):
    """Saldo und Historie zu einer Electrum-Kennung.

    Die Historie kann sehr gross werden (Genesis-Adresse: 65.311 Eintraege,
    von Fulcrum in rund 7 s geliefert). Deshalb: der Saldo kommt IMMER, die
    Liste wird ab LISTENGRENZE nur noch gezaehlt statt ausgeliefert.
    """
    host, port = ziel()
    if not host or not port:
        return None
    try:
        async with Verbindung(host, port, zeitlimit) as v:
            saldo = await v.frage("blockchain.scripthash.get_balance", [kennung])
            verlauf = await v.frage("blockchain.scripthash.get_history", [kennung])
    except (OSError, asyncio.TimeoutError, ValueError, TypeError,
            ElektrumFehler, json.JSONDecodeError):
        return None

    verlauf = verlauf or []
    bestaetigt = [e for e in verlauf if (e.get("height") or 0) > 0]
    offen = [e for e in verlauf if (e.get("height") or 0) <= 0]
    # Neueste zuerst; unbestaetigte ganz nach oben.
    bestaetigt.sort(key=lambda e: e.get("height") or 0, reverse=True)

    return {
        "bestaetigt_sat": (saldo or {}).get("confirmed", 0),
        "offen_sat": (saldo or {}).get("unconfirmed", 0),
        "anzahl": len(verlauf),
        "anzahl_offen": len(offen),
        "zu_gross": len(verlauf) > LISTENGRENZE,
        "grenze": LISTENGRENZE,
        "verlauf": (offen + bestaetigt)[:LISTENGRENZE]
                   if len(verlauf) <= LISTENGRENZE else [],
    }
