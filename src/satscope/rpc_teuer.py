"""Teure RPC-Methoden - NUR der Sammler darf dieses Modul importieren.

Gemessen an .67 (15.08.2026):
    gettxoutsetinfo   58 s   hebt die Systemlast von 0,76 auf 1,68
    scantxoutset      48 s
    getblock <h> 3    0,29-0,42 s   ABER 13,9 MB JSON je Antwort
    getrawmempool 1   0,35-0,45 s   13,4 MB
    getblocktemplate  0,07-0,10 s   4 MB

Die grossen Antworten sind nicht wegen ihrer Laufzeit gefaehrlich, sondern wegen
ihres Speichers: 13,9 MB JSON werden als Python-Struktur leicht 150-250 MB. Der
Umbrel-Knoten hat als Swap nur zram, also keinen Ausweg auf Platte - zwei solche
Antworten gleichzeitig koennen den OOM-Killer auf bitcoind hetzen.

Deshalb Schranke der Groesse 1: eine grosse Antwort nach der anderen, sofort auf
ihr Aggregat reduziert, Rohobjekt freigeben.
"""
import asyncio

from .rpc import BILLIG, Tor

TEUER = frozenset({
    "getblock",
    "getrawmempool",
    "getblocktemplate",
    "gettxoutsetinfo",
    "scantxoutset",
    "scanblocks",
    "getnodeaddresses",
})


class SammlerTor(Tor):
    """Tor mit billigen UND teuren Methoden, aber nur EIN grosser Abruf zugleich."""

    def __init__(self, **kw):
        super().__init__(erlaubt=BILLIG | TEUER, zeitlimit=120.0, **kw)
        self._gross = asyncio.Semaphore(1)

    async def ruf(self, methode, *argumente):
        if methode in TEUER:
            async with self._gross:
                return await super().ruf(methode, *argumente)
        return await super().ruf(methode, *argumente)
