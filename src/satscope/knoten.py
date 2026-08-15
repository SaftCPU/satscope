"""Erhebt den Zustand des eigenen Knotens - ausschliesslich billige Aufrufe.

Jeder Wert wird EINZELN abgesichert: faellt ein Aufruf aus, fehlt genau diese
Zahl und die Seite zeigt dort einen Strich. Eine halb erreichbare Quelle darf
nicht die ganze Seite leeren - und erst recht nichts erfinden.
"""
import asyncio
import time

from .rpc import RpcFehler


async def _sicher(tor, methode, *argumente):
    """Ruft auf und liefert None statt zu werfen."""
    try:
        return await tor.ruf(methode, *argumente)
    except (RpcFehler, OSError, asyncio.TimeoutError):
        return None


async def zustand(tor):
    """Alles, was die Startseite braucht, in einem Rutsch.

    Die Aufrufe laufen nebenlaeufig; das Tor deckelt sie selbst auf vier
    gleichzeitige, damit wir bitcoinds Warteschlange nicht belegen.
    """
    kette, mempool, mining, netz = await asyncio.gather(
        _sicher(tor, "getblockchaininfo"),
        _sicher(tor, "getmempoolinfo"),
        _sicher(tor, "getmininginfo"),
        _sicher(tor, "getnetworkinfo"),
    )

    hoehe = (kette or {}).get("blocks")
    kopfzeilen = (kette or {}).get("headers")
    # "Aufholen" heisst: wir kennen Kopfzeilen, die wir noch nicht verarbeitet
    # haben. initialblockdownload allein genuegt nicht - es steht auch nach
    # einem laengeren Stillstand noch auf false.
    holt_auf = bool((kette or {}).get("initialblockdownload")) or (
        hoehe is not None and kopfzeilen is not None and kopfzeilen - hoehe > 1)

    block_zeit = None
    if hoehe is not None:
        h = await _sicher(tor, "getblockhash", hoehe)
        if h:
            kopf = await _sicher(tor, "getblockheader", h)
            block_zeit = (kopf or {}).get("time")

    return {
        "erreichbar": kette is not None,
        "holt_auf": holt_auf,
        "hoehe": hoehe,
        "kopfzeilen": kopfzeilen,
        "block_zeit": block_zeit,
        "block_alter": (int(time.time()) - block_zeit) if block_zeit else None,
        "kette": (kette or {}).get("chain"),
        "platte_bytes": (kette or {}).get("size_on_disk"),
        "mempool_bytes": (mempool or {}).get("bytes"),
        "mempool_anzahl": (mempool or {}).get("size"),
        "mempool_min_gebuehr": (mempool or {}).get("mempoolminfee"),
        "schwierigkeit": (mining or {}).get("difficulty"),
        "verbindungen": (netz or {}).get("connections"),
        "eingehend": (netz or {}).get("connections_in"),
        "ausgehend": (netz or {}).get("connections_out"),
    }
