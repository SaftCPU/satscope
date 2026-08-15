"""Die letzten Bloecke und der wartende Mempool - Datenerhebung fuer das Band.

Liefert ausschliesslich ROHZAHLEN. Kein Text, keine Einheit, keine Rundung auf
Anzeigestellen: der Browser kennt die Sprache und formatiert selbst (deutsch
1.234,56 gegen englisch 1,234.56). Was hier gerechnet wird, sind nur Groessen,
die aus den erhobenen Zahlen folgen - nie eine geschaetzte.

WARUM EIN ZWISCHENSPEICHER PFLICHT IST
Ein Block kostet getblockhash (<1 ms) + getblockstats (19-30 ms), zusammen rund
25 ms. Zwoelf Bloecke sind damit ~300 ms seriell; durch die Viererschranke des
Tores bleiben rechnerisch ~80-90 ms. Das Band fragt alle 5 s - und zwar aus
JEDEM offenen Browserfenster. Ohne Speicher waeren das bei drei Fenstern 36
getblockstats alle fuenf Sekunden auf einem Knoten, auf dem echtes Geld liegt.
Die Bloecke aendern sich aber nur, wenn sich die Spitze aendert. Also: erheben
nur bei neuem Spitzenhash, sonst ausliefern, was daliegt.

WARUM DER SPITZENHASH UND NICHT DIE HOEHE
Bei einer Reorganisation bleibt die Hoehe gleich, waehrend sich der Inhalt
aendert. Ein Speicher, der auf die Hoehe hoert, wuerde den verwaisten Block
weiterzeigen - unbemerkt, weil alle Zahlen plausibel aussehen.

WARUM getblockstats UND NICHT getblock
getblockstats bringt Groesse, Anzahl, Gebuehrenspanne, Belohnung und Perzentile
in EINEM Aufruf. getblock mit Detailstufe 2 liefert dieselbe Auskunft als 13,9 MB
JSON - im Web-Prozess verboten (s. rpc_teuer.py).

REIHENFOLGE: `bloecke` kommt AELTESTER ZUERST, der neueste steht am Ende. So
zeichnet der Browser das Band von links nach rechts, ohne die Liste zu drehen.
"""
import asyncio
import time

from .rpc import RpcFehler

# Zwoelf Bloecke sind rund zwei Stunden Kette - genug, um "teuer" von "gerade
# eben teuer geworden" zu unterscheiden, und noch eine Zeile auf dem Schirm.
ANZAHL = 12

# Der Mempool aendert sich staendig, die Bloecke nicht. Er bekommt deshalb einen
# eigenen, kurzen Speicher. 4 s liegen unter dem Abfragetakt von 5 s - ein
# einzelner Browser sieht also immer frische Zahlen -, fangen aber die zweite
# und dritte gleichzeitig offene Seite ab.
MEMPOOL_ALTER = 4.0

# Die Gebuehrenschaetzung folgt den Bloecken, nicht dem Sekundentakt: bitcoind
# rechnet sie aus bestaetigten Bloecken, nicht aus dem Augenblick. 30 s Alter
# liegen damit unterhalb ihrer eigenen Aufloesung und sparen drei Aufrufe je
# Abfrage - aus 37 ms Knotenzeit je Abfrage werden so 16 ms.
SCHAETZUNG_ALTER = 30.0

# War eine Erhebung unvollstaendig (ein Aufruf fiel aus), darf sie sich nicht bis
# zum naechsten Block einbrennen. Nach dieser Frist wird es noch einmal versucht.
NACHFASSEN = 60.0

# Ein Block passt in 4.000.000 Gewichtseinheiten. Daraus wird die Fuellung.
BLOCKGEWICHT = 4000000

# Der Mempool zaehlt virtuelle Bytes; 1.000.000 vB sind ein Block voll Arbeit.
BLOCK_VBYTES = 1000000

# Gebuehrenschaetzung fuer den wartenden Block. estimatesmartfee kostet 7 ms.
# Drei Ziele reichen: der naechste Block, eine halbe Stunde, eine Stunde.
ZIELE = (1, 3, 6)

_bloecke = {"hash": None, "liste": [], "erhoben": 0.0, "vollstaendig": False}
_mempool = {"wert": None, "erhoben": 0.0}
_schaetzung = {"wert": None, "erhoben": 0.0}

# Ohne Schloss erheben drei gleichzeitig eintreffende Anfragen dreimal dasselbe.
_schloss = asyncio.Lock()
_mempool_schloss = asyncio.Lock()


async def _sicher(tor, methode, *argumente):
    """Ruft auf und liefert None statt zu werfen.

    Absichtlich eine eigene Kopie und kein Import von knoten._sicher: ein
    privater Name aus einem fremden Modul ist eine Fessel, die beim naechsten
    Umbau reisst. Das Verhalten ist dasselbe - faellt ein Aufruf aus, fehlt
    genau dieser Wert, nicht die Seite.
    """
    try:
        return await tor.ruf(methode, *argumente)
    except (RpcFehler, OSError, asyncio.TimeoutError):
        return None


def _wert(daten, name):
    """Eine Zahl aus einer RPC-Antwort - oder None, wenn sie nicht da ist.

    Kein Ersatzwert, keine Null: eine fehlende Zahl muss als Strich sichtbar
    werden, sonst zeigt das Band eine Gebuehr von 0 sat/vB, die es nie gab.
    """
    if not isinstance(daten, dict):
        return None
    w = daten.get(name)
    return w if isinstance(w, (int, float)) and not isinstance(w, bool) else None


def _median(werte):
    werte = sorted(w for w in werte if w is not None)
    if not werte:
        return None
    mitte = len(werte) // 2
    if len(werte) % 2:
        return werte[mitte]
    return (werte[mitte - 1] + werte[mitte]) / 2.0


def _satvb(btc_je_kvb):
    """BTC/kvB (so rechnet bitcoind Gebuehren) in sat/vB (so liest sie der Mensch).

    100.000.000 sat je BTC, geteilt durch 1.000 vB je kvB, macht mal 100.000.
    Die Rundung auf fuenf Stellen faengt nur den Fliesskomma-Schmutz ab
    (0.00001 * 1e5 ergibt sonst 1.0000000000000002), sie ist keine Anzeigerundung.
    """
    if btc_je_kvb is None or btc_je_kvb < 0:
        return None
    return round(btc_je_kvb * 100000.0, 5)


async def _ein_block(tor, hoehe):
    """Ein Block als schlichtes dict. Jeder Wert einzeln abgesichert.

    Faellt getblockhash aus, bleibt die Hoehe stehen und alles andere ist None -
    das Band behaelt seinen Platz und zeigt dort Striche. Faellt nur
    getblockstats aus, ist wenigstens der Hash da und der Block bleibt anklickbar.
    """
    block = {"hoehe": hoehe, "hash": None, "zeit": None, "groesse": None,
             "gewicht": None, "txs": None, "gebuehr_min": None,
             "gebuehr_max": None, "gebuehr_median": None, "gebuehr_schnitt": None,
             "perzentile": None, "median_sat": None, "belohnung": None,
             "gebuehren": None, "ein": None, "aus": None, "abstand": None,
             "billigster": False, "teuerster": False}

    hasch = await _sicher(tor, "getblockhash", hoehe)
    if not isinstance(hasch, str):
        return block
    block["hash"] = hasch

    # Ueber den Hash und nicht ueber die Hoehe: faellt zwischen beiden Aufrufen
    # eine Reorganisation, antwortet getblockstats auf den verwaisten Hash gar
    # nicht - besser ein Strich als Zahlen aus einer anderen Kette unter einer
    # Hoehe, die inzwischen jemand anderem gehoert.
    st = await _sicher(tor, "getblockstats", hasch)
    if not isinstance(st, dict):
        return block

    block["zeit"] = _wert(st, "time")
    block["groesse"] = _wert(st, "total_size")
    block["gewicht"] = _wert(st, "total_weight")
    block["txs"] = _wert(st, "txs")
    block["gebuehr_min"] = _wert(st, "minfeerate")
    block["gebuehr_max"] = _wert(st, "maxfeerate")
    block["gebuehr_schnitt"] = _wert(st, "avgfeerate")
    block["median_sat"] = _wert(st, "medianfee")
    block["belohnung"] = _wert(st, "subsidy")
    block["gebuehren"] = _wert(st, "totalfee")
    block["ein"] = _wert(st, "ins")
    block["aus"] = _wert(st, "outs")

    # ⚠️ medianfee ist die mittlere ABSOLUTE Gebuehr einer Transaktion in sat -
    # nicht ihr Preis je Byte. Faerben darf man danach nicht: ein Block voller
    # grosser, billiger Transaktionen saehe teurer aus als einer mit kleinen
    # teuren. Der Preis je Byte steht im 50. Perzentil der Gewichtseinheiten.
    p = st.get("feerate_percentiles")
    if isinstance(p, list) and len(p) == 5 and all(
            isinstance(x, (int, float)) for x in p):
        block["perzentile"] = list(p)
        block["gebuehr_median"] = p[2]
    return block


async def _erhebe_bloecke(tor, spitze):
    """Die letzten ANZAHL Bloecke, nebenlaeufig erhoben.

    Nebenlaeufig, aber nicht ungebremst: das Tor laesst selbst nur vier Aufrufe
    gleichzeitig durch, damit bitcoinds rpcworkqueue frei bleibt.
    """
    von = max(0, spitze - ANZAHL + 1)
    liste = list(await asyncio.gather(
        *[_ein_block(tor, h) for h in range(von, spitze + 1)]))

    # Abstand zum Block davor. Blockzeiten sind NICHT streng steigend (ein Miner
    # darf zurueckdatieren, solange die Median-Zeit-Regel haelt), der Wert kann
    # also negativ sein. Er wird trotzdem roh weitergereicht - der Browser
    # entscheidet, ob er ihn zeigt. Erfinden waere schlimmer als weglassen.
    for i in range(1, len(liste)):
        vorher, jetzt = liste[i - 1]["zeit"], liste[i]["zeit"]
        if vorher is not None and jetzt is not None:
            liste[i]["abstand"] = jetzt - vorher

    # Die Einordnung, die den ganzen Entwurf traegt: nicht "3,2 sat/vB", sondern
    # "3,2 sat/vB - der billigste der letzten zwoelf". Nur markieren, wenn es
    # ueberhaupt etwas zu unterscheiden gibt.
    mediane = [(b["gebuehr_median"], b["hoehe"]) for b in liste
               if b["gebuehr_median"] is not None]
    if len(mediane) >= 3 and min(mediane)[0] != max(mediane)[0]:
        billig, teuer = min(mediane)[1], max(mediane)[1]
        for b in liste:
            b["billigster"] = b["hoehe"] == billig
            b["teuerster"] = b["hoehe"] == teuer
    return liste


def _fenster(liste):
    """Kennzahlen ueber alle erhobenen Bloecke - der Bezugsrahmen der Einzelzahl."""
    mediane = [b["gebuehr_median"] for b in liste if b["gebuehr_median"] is not None]
    abstaende = [b["abstand"] for b in liste
                 if b["abstand"] is not None and b["abstand"] > 0]
    txs = [b["txs"] for b in liste if b["txs"] is not None]
    groessen = [b["groesse"] for b in liste if b["groesse"] is not None]
    return {
        "gemessen": len(mediane),
        "median_min": min(mediane) if mediane else None,
        "median_max": max(mediane) if mediane else None,
        "median_mitte": _median(mediane),
        "abstand_schnitt": (sum(abstaende) / len(abstaende)) if abstaende else None,
        "txs_gesamt": sum(txs) if txs else None,
        "groesse_gesamt": sum(groessen) if groessen else None,
    }


async def _schaetzungen(tor):
    """Was der Eintritt kostet, je Ziel. Eigener Speicher, s. SCHAETZUNG_ALTER.

    Braucht kein eigenes Schloss: der einzige Aufrufer laeuft bereits unter
    _mempool_schloss.
    """
    jetzt = time.monotonic()
    if (_schaetzung["wert"] is None
            or jetzt - _schaetzung["erhoben"] > SCHAETZUNG_ALTER):
        antworten = await asyncio.gather(
            *[_sicher(tor, "estimatesmartfee", z) for z in ZIELE])
        # estimatesmartfee antwortet bei zu duenner Datenlage mit "errors" und
        # ohne feerate. Dann fehlt die Zahl - geraten wird nicht.
        _schaetzung["wert"] = [{"ziel": z, "satvb": _satvb(_wert(a, "feerate"))}
                               for z, a in zip(ZIELE, antworten)]
        _schaetzung["erhoben"] = jetzt
    return _schaetzung["wert"]


async def _erhebe_mempool(tor):
    """Der wartende Block: Fuellstand und was der Eintritt gerade kostet."""
    mp, ziele = await asyncio.gather(
        _sicher(tor, "getmempoolinfo"), _schaetzungen(tor))

    anzahl = _wert(mp, "size")
    bytes_ = _wert(mp, "bytes")
    speicher = _wert(mp, "usage")
    speicher_max = _wert(mp, "maxmempool")

    return {
        "anzahl": anzahl,
        "bytes": bytes_,
        "speicher": speicher,
        "speicher_max": speicher_max,
        "speicher_anteil": (speicher / speicher_max)
                           if speicher is not None and speicher_max else None,
        "min_gebuehr": _satvb(_wert(mp, "mempoolminfee")),
        # total_fee gibt es erst ab Core 25; fehlt es, fehlt es eben.
        "gebuehren_btc": _wert(mp, "total_fee"),
        # Wieviele Bloecke Arbeit warten. Reine Division, keine Prognose.
        "blockaequivalent": (bytes_ / BLOCK_VBYTES) if bytes_ is not None else None,
        # Fuellung des GEZEICHNETEN Wartenden: mehr als voll geht nicht, der
        # Ueberhang steht als Blockaequivalent daneben.
        "fuellung": min(1.0, bytes_ / BLOCK_VBYTES) if bytes_ is not None else None,
        "schaetzung": ziele,
    }


async def kette(tor):
    """Alles, was das Band braucht - Bloecke aus dem Speicher, Mempool frisch."""
    info = await _sicher(tor, "getblockchaininfo")
    spitze = _wert(info, "blocks")
    spitzen_hash = (info or {}).get("bestblockhash")
    if spitze is not None:
        spitze = int(spitze)

    async with _schloss:
        jetzt = time.monotonic()
        veraltet = (spitzen_hash is not None
                    and spitzen_hash != _bloecke["hash"])
        nachfassen = (not _bloecke["vollstaendig"]
                      and jetzt - _bloecke["erhoben"] > NACHFASSEN)
        if spitze is not None and (veraltet or nachfassen or not _bloecke["liste"]):
            liste = await _erhebe_bloecke(tor, spitze)
            _bloecke["liste"] = liste
            _bloecke["hash"] = spitzen_hash
            _bloecke["erhoben"] = jetzt
            # Vollstaendig heisst: jeder Block hat seine Zahlen. Nur dann darf
            # das Ergebnis bis zum naechsten Block liegenbleiben.
            _bloecke["vollstaendig"] = bool(liste) and all(
                b["gebuehr_median"] is not None for b in liste)
        liste = list(_bloecke["liste"])

    async with _mempool_schloss:
        jetzt = time.monotonic()
        if _mempool["wert"] is None or jetzt - _mempool["erhoben"] > MEMPOOL_ALTER:
            _mempool["wert"] = await _erhebe_mempool(tor)
            _mempool["erhoben"] = jetzt
        mempool = _mempool["wert"]

    return {
        "erreichbar": info is not None,
        "spitze": spitze,
        "spitze_hash": spitzen_hash,
        "anzahl": ANZAHL,
        "blockgewicht": BLOCKGEWICHT,
        "block_vbytes": BLOCK_VBYTES,
        "bloecke": liste,
        "fenster": _fenster(liste),
        "mempool": mempool,
        # Die Uhr des Servers. Der Browser rechnet damit seinen eigenen Versatz
        # heraus - sonst zeigt eine um zwei Minuten vorgehende Uhr im Wohnzimmer
        # ein Blockalter von "-2 Min.", und das sieht nach einem Fehler aus.
        "jetzt": int(time.time()),
    }


def leeren():
    """Speicher vergessen - fuer Tests. Im Betrieb ruft das niemand."""
    _bloecke.update({"hash": None, "liste": [], "erhoben": 0.0,
                     "vollstaendig": False})
    _mempool.update({"wert": None, "erhoben": 0.0})
    _schaetzung.update({"wert": None, "erhoben": 0.0})


def handler(tor):
    """Baut den JSON-Handler fuer /api/kette.

    Eine Fabrik und kein blosser Handler, damit das Tor von aussen
    hereingereicht wird: ein zweites Tor im Modul haette eine zweite
    Viererschranke - zusammen acht gleichzeitige Aufrufe auf einem Knoten, der
    nur Gast bei uns ist. Ein Import von web.py waere zirkulaer.

        Route("/api/kette", kette.handler(TOR))
    """
    from starlette.responses import JSONResponse

    async def api_kette(request):
        daten = await kette(tor)
        # no-store: die Antwort ist bereits durch den Modulspeicher gedeckelt,
        # ein zusaetzlicher Browser-Cache wuerde nur alte Bloecke einfrieren.
        return JSONResponse(daten, headers={"Cache-Control": "no-store"})

    return api_kette
