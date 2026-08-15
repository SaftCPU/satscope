"""Das EINZIGE Tor zu bitcoind - mit Kostenklassen.

Warum so streng: gemessen am Knoten (15.08.2026) braucht gettxoutsetinfo
**58 Sekunden** und hebt die Systemlast von 0,76 auf 1,68; scantxoutset 48 s;
listunspent einer Grossadresse 5,9 s. Ein einziger unbedachter Aufruf aus einem
Web-Handler macht daraus einen Denial-of-Service gegen den eigenen Knoten.

Deshalb: Der Web-Prozess bekommt ein Tor, das die teuren Methoden gar nicht
KENNT. Sie stehen in rpc_teuer.py, das nur der Sammler importiert. Ein Verstoss
ist damit ein Fehler beim Start, kein Ausfall im Betrieb.
"""
import asyncio
import os

# Billig: alles unter ~50 ms, an .67 gemessen. Darf aus jedem Web-Handler.
BILLIG = frozenset({
    "getblockchaininfo",      # 9 ms
    "getmempoolinfo",         # 7 ms
    "getmininginfo",          # 7 ms
    "getnetworkinfo",         # 15 ms
    "getnettotals",           # 7 ms
    "getpeerinfo",            # 8-15 ms
    "getdeploymentinfo",      # 7-10 ms
    "getblockstats",          # 19-30 ms
    "getblockhash",
    "getblockheader",
    "getrawtransaction",      # 19-28 ms
    "gettxspendingprevout",   # 9 ms
    "getmempoolentry",        # 8 ms
    "getmempoolcluster",      # 8 ms
    "estimatesmartfee",       # 7 ms
    "estimaterawfee",         # 15 ms
    "getchaintips",           # 114 ms - Grenzfall, aber selten aufgerufen
})


class RpcFehler(Exception):
    pass


class NichtErlaubt(RpcFehler):
    """Aufruf einer Methode, die dieses Tor nicht fuehrt."""


class Tor:
    """Ein RPC-Tor mit fester Methoden-Allowlist.

    Gleichzeitige Aufrufe sind gedeckelt: bitcoind hat eine rpcworkqueue, und
    wir sind nur Gast auf diesem Knoten.
    """

    def __init__(self, erlaubt=BILLIG, gleichzeitig=4, zeitlimit=5.0):
        self.erlaubt = frozenset(erlaubt)
        self._schranke = asyncio.Semaphore(gleichzeitig)
        self.zeitlimit = zeitlimit
        self.host = os.environ.get("BITCOIN_RPC_HOST", "127.0.0.1")
        self.port = int(os.environ.get("BITCOIN_RPC_PORT", "8332"))
        self.benutzer = os.environ.get("BITCOIN_RPC_USER", "")
        self.geheim = os.environ.get("BITCOIN_RPC_PASS", "")

    def kennt(self, methode):
        return methode in self.erlaubt

    async def ruf(self, methode, *argumente):
        if methode not in self.erlaubt:
            raise NichtErlaubt(
                "%s ist in diesem Tor nicht erlaubt (Kostenklasse)" % methode)
        async with self._schranke:
            return await self._senden(methode, list(argumente))

    async def _senden(self, methode, argumente):
        # httpx erst hier importieren, damit Selbsttests ohne Abhaengigkeiten laufen
        import httpx
        nutz = {"jsonrpc": "2.0", "id": "satscope",
                "method": methode, "params": argumente}
        try:
            async with httpx.AsyncClient(timeout=self.zeitlimit) as k:
                antwort = await k.post(
                    "http://%s:%d/" % (self.host, self.port),
                    json=nutz, auth=(self.benutzer, self.geheim))
        except httpx.HTTPError as e:
            # ALLE Transportfehler werden hier in RpcFehler uebersetzt.
            # Sonst muesste jeder Aufrufer httpx kennen - und httpx.ConnectError
            # ist KEIN OSError, weshalb ein nicht erreichbarer Knoten die
            # Startseite mit HTTP 500 abgeschossen hat statt einen Strich zu
            # zeigen (gefunden im Rendertest, 15.08.2026).
            raise RpcFehler("%s nicht erreichbar: %s" % (methode, type(e).__name__))
        if antwort.status_code != 200:
            raise RpcFehler("HTTP %d bei %s" % (antwort.status_code, methode))
        d = antwort.json()
        if d.get("error"):
            raise RpcFehler("%s: %s" % (methode, d["error"]))
        return d.get("result")
