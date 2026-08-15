"""Die Transaktionsseite /tx/<txid> - eine Transaktion in ganzen Saetzen.

Der Leitgedanke des Projekts, hier angewandt: eine Transaktion ist kein
Datenblatt, sondern eine Geschichte. Ob sie ersetzt werden darf, ob sie zu
billig ist, ob ihre Ausgaenge schon weitergereicht wurden - das gehoert ueber
die Tabelle, nicht in sie.

Kostenrahmen (am Knoten gemessen, siehe rpc.py): getrawtransaction 19-28 ms,
getmempoolentry 8 ms, getmempoolcluster 8 ms, gettxspendingprevout 9 ms,
getblockstats 19-30 ms, getblockheader <5 ms, estimatesmartfee 7 ms. Alles
laeuft nebenlaeufig, das Tor deckelt selbst auf vier gleichzeitige Aufrufe.
Eine Seitenansicht kostet den Knoten damit gut eine Zehntelsekunde - und
keinen einzigen teuren Aufruf. getblock/getrawmempool kommen hier NICHT vor.

Aufteilung nach aussen:
    transaktion(tor, txid)  -> reine Daten, sprachfrei, jeder Wert einzeln
                               abgesichert (None = fehlt = Strich)
    befunde(daten, t)       -> fertige Saetze in der Sprache des Nutzers
    spanne(sekunden, t)     -> Zeitspanne als Text ("1 Std. 20 Min.")
Zahlen und Texte entstehen also erst beim Rendern, wo `t` die Sprache kennt.
"""
import asyncio
import hashlib
import time

from . import elektrum
from .rpc import RpcFehler

# BIP125: alles UNTER 0xfffffffe signalisiert Ersetzbarkeit. 0xfffffffe selbst
# ist "nicht ersetzbar, aber nLockTime wirkt" - der Unterschied ist genau ein
# Bit und wird gern verwechselt.
SEQ_FINAL = 0xFFFFFFFF
SEQ_RBF = 0xFFFFFFFE

# Ein Block fasst 1.000.000 vByte (4.000.000 Gewichtseinheiten).
BLOCK_VBYTE = 1000000

# Coinbase-Ausgaenge sind 100 Bloecke gesperrt (Konsensregel, nicht Konvention).
REIFE = 100

# Sperrzeiten unter dieser Schwelle sind Blockhoehen, darueber Unix-Zeit.
SPERRZEIT_GRENZE = 500000000

# Ziele fuer estimatesmartfee. Sieben Aufrufe a 7 ms, die das Tor auf vier
# gleichzeitige deckelt: rund 14 ms Wartezeit fuer die Aussage "reicht fuer
# eine Bestaetigung in etwa N Bloecken".
ZIELE = (1, 2, 3, 6, 12, 24, 144)

# So viele zurueckliegende Bloecke werden als "was zuletzt verlangt wurde"
# herangezogen. Drei mal getblockstats = rund 90 ms, nebenlaeufig.
VERGLEICHSBLOECKE = 3

# gettxspendingprevout durchsucht fuer JEDEN Aussenpunkt den Mempool. Bei einer
# Auszahlungsrunde mit tausend Ausgaengen waere das aus einem Web-Handler
# heraus unhoeflich gegenueber einem Knoten, auf dem echtes Geld liegt.
MAX_AUSGAENGE_PREVOUT = 100

# Ebenso beim Index: je verschiedenem Skript eine Electrum-Abfrage. Mehr als
# ein Dutzend lohnt die Wartezeit nicht - dann bleibt der Ausgabestand offen.
MAX_SKRIPTE = 12


# --------------------------------------------------------------- Werkzeug
async def _versuch(tor, methode, *argumente):
    """(Ergebnis, Fehlertext). Wie knoten._sicher(), aber der Grund bleibt.

    Nur der erste Aufruf braucht ihn: an seinem Fehlertext haengt, ob wir dem
    Nutzer "nicht gefunden" oder "dein Knoten fuehrt keinen Index" sagen.
    """
    try:
        return await tor.ruf(methode, *argumente), None
    except (RpcFehler, OSError, asyncio.TimeoutError) as e:
        return None, str(e)


async def _sicher(tor, methode, *argumente):
    """Ruft auf und liefert None statt zu werfen - genau wie knoten._sicher().

    Bewusst hier noch einmal und nicht importiert: die Regel "jeder Wert
    einzeln abgesichert" soll in jedem Modul lesbar sein, das sie anwendet.
    """
    wert, _ = await _versuch(tor, methode, *argumente)
    return wert


def _grund(fehler):
    """Fehlertext von bitcoind in einen Grund uebersetzen, den man erklaeren kann.

    Der Trick mit txindex: getindexinfo ist uns nicht erlaubt, aber bitcoind
    verraet den Zustand selbst. Es antwortet
        "No such mempool transaction. Use -txindex or provide a block hash ..."
    NUR dann, wenn kein Transaktionsindex laeuft; mit Index heisst es schlicht
    "No such mempool or blockchain transaction". Das Wort txindex im Fehlertext
    ist also eine verlaessliche Auskunft und keine Vermutung.
    """
    text = (fehler or "").lower()
    if "nicht erreichbar" in text or text.startswith("http "):
        return "unerreichbar"
    if "txindex" in text:
        return "kein_txindex"
    return "unbekannt"


def _sat(btc):
    """BTC-Fliesskommazahl aus dem RPC in ganze Satoshi.

    Fliesskomma hat bei Geld nichts zu suchen - hier ist die Umrechnung aber
    beweisbar exakt: 21 Mio BTC sind 2,1e15 Satoshi, ein double traegt
    2^53 = 9,0e15 ganze Zahlen ohne Verlust. Jeder ueberhaupt existierende
    Betrag liegt unter dieser Grenze, das Runden trifft also immer die
    richtige ganze Zahl. AB HIER wird nur noch ganzzahlig gerechnet; geteilt
    wird erst in der Anzeige.
    """
    if btc is None:
        return None
    try:
        return int(round(float(btc) * 100000000))
    except (TypeError, ValueError):
        return None


def _hex64(wert):
    if not isinstance(wert, str) or len(wert) != 64:
        return False
    return all(z in "0123456789abcdef" for z in wert.lower())


def _scripthash(skript_hex):
    """Electrums Kennung aus dem rohen scriptPubKey.

    adresse.py rechnet dasselbe aus einer Adresse aus; hier liegt das Skript
    schon fertig in der RPC-Antwort. Das ist nicht nur kuerzer, es geht auch
    fuer Ausgaenge, die gar keine Adressform haben (P2PK, blankes Multisig).
    """
    try:
        roh = bytes.fromhex(skript_hex)
    except (ValueError, TypeError):
        return None
    return hashlib.sha256(roh).digest()[::-1].hex()


def _typ_name(typ):
    """Cores Typnamen als die Kuerzel, die jeder Explorer benutzt.

    Bewusst NICHT im Textkatalog: "P2WPKH" heisst in beiden Sprachen gleich,
    ein uebersetztes Kuerzel waere schlechter, nicht besser.
    """
    return {
        "pubkeyhash": "P2PKH",
        "scripthash": "P2SH",
        "witness_v0_keyhash": "P2WPKH",
        "witness_v0_scripthash": "P2WSH",
        "witness_v1_taproot": "P2TR",
        "witness_unknown": "Witness v?",
        "pubkey": "P2PK",
        "multisig": "Multisig",
        "nulldata": "OP_RETURN",
        "anchor": "P2A",
    }.get(typ, typ or "?")


def _lesbar(roh, mindest=4, grenze=64):
    """Laengster druckbarer ASCII-Lauf in einem Haufen Bytes.

    Coinbase-Eingaenge und OP_RETURN-Ausgaenge tragen Text zwischen Binaerdaten
    (Pool-Kennungen, Nachrichten). Der laengste zusammenhaengende Lauf trifft
    ihn zuverlaessiger als jede Quote ueber das Ganze.
    """
    beste, jetzt = "", ""
    for b in roh or b"":
        if 32 <= b < 127:
            jetzt += chr(b)
            continue
        if len(jetzt) > len(beste):
            beste = jetzt
        jetzt = ""
    if len(jetzt) > len(beste):
        beste = jetzt
    beste = beste.strip()
    return beste[:grenze] if len(beste) >= mindest else None


def _opreturn(skript_hex):
    """(Anzahl Datenbytes, lesbarer Text) eines OP_RETURN-Ausgangs."""
    try:
        roh = bytes.fromhex(skript_hex or "")
    except (ValueError, TypeError):
        return None, None
    if not roh or roh[0] != 0x6A:
        return None, None
    i, stuecke = 1, []
    while i < len(roh):
        op = roh[i]
        i += 1
        if 1 <= op <= 75:
            laenge = op
        elif op == 0x4C and i < len(roh):          # OP_PUSHDATA1
            laenge = roh[i]
            i += 1
        elif op == 0x4D and i + 1 < len(roh):      # OP_PUSHDATA2
            laenge = int.from_bytes(roh[i:i + 2], "little")
            i += 2
        else:
            break
        stuecke.append(roh[i:i + laenge])
        i += laenge
    daten = b"".join(stuecke)
    return len(daten), _lesbar(daten, mindest=3)


def spanne(sekunden, t):
    """Zeitspanne als Text: "1 Std. 20 Min." / "1 h 20 min".

    web.py hat ein _dauer_text() - das bildet aber die ANDERE Grammatik
    ("vor 20 Min."). Ein Wartezeit-Satz braucht die blanke Spanne, sonst
    stuende dort "Sie wartet seit vor 20 Min.".
    """
    if sekunden is None:
        return t.t("value.missing")
    minuten = max(0, int(sekunden)) // 60
    if minuten < 1:
        return t.t("tx.span.now")
    if minuten < 60:
        return t.t("tx.span.min", n=t.zahl(minuten))
    stunden, rest = divmod(minuten, 60)
    if stunden < 48:
        if rest:
            return t.t("tx.span.hour_min", n=t.zahl(stunden), m=t.zahl(rest))
        return t.t("tx.span.hour", n=t.zahl(stunden))
    # Ab hier immer mindestens zwei Tage - eine Einzahl-Fassung braucht es nicht.
    tage, reststunden = divmod(stunden, 24)
    if reststunden:
        return t.t("tx.span.day_hour", n=t.zahl(tage), h=t.zahl(reststunden))
    return t.t("tx.span.day", n=t.zahl(tage))


# --------------------------------------------------------------- Teilerhebungen
async def _schaetzungen(tor):
    """estimatesmartfee je Ziel, umgerechnet in sat/vB. Fehlende Ziele fehlen."""
    ergebnisse = await asyncio.gather(
        *[_sicher(tor, "estimatesmartfee", z) for z in ZIELE])
    raus = {}
    for ziel, e in zip(ZIELE, ergebnisse):
        rate = (e or {}).get("feerate")
        if rate:
            # BTC/kvB -> sat/vB: 1e8 Satoshi je BTC, geteilt durch 1000 vByte.
            raus[ziel] = float(rate) * 100000.0
    return raus


async def _letzte_bloecke(tor, hoehe):
    """Was die letzten Bloecke tatsaechlich verlangt haben, in sat/vB.

    Genommen wird das 10. Perzentil je Block, nicht minfeerate: viele Pools
    packen eigene Transaktionen ohne Gebuehr in ihren Block, dann ist
    minfeerate 0 und der Vergleich wertlos. Das 10. Perzentil beschreibt den
    guenstigen Rand des Blocks, ohne von einem Einzelfall gekippt zu werden.
    """
    if hoehe is None:
        return None
    hoehen = [hoehe - i for i in range(VERGLEICHSBLOECKE) if hoehe - i >= 0]
    stats = await asyncio.gather(
        *[_sicher(tor, "getblockstats", h, ["feerate_percentiles"]) for h in hoehen])
    unten = []
    for s in stats:
        p = (s or {}).get("feerate_percentiles")
        if isinstance(p, list) and p:
            unten.append(float(p[0]))
    return min(unten) if unten else None


def _chunk_lesen(antwort, txid):
    """Den Chunk aus getmempoolcluster herausschaelen - betont vorsichtig.

    getmempoolcluster ist neu (Core 31) und die einzige Stelle, an der ein
    Aussenstehender die tatsaechliche Einbau-Einheit des Miners sieht: nicht
    die einzelne Transaktion, sondern das Stueck, das er als Ganzes nimmt oder
    liegen laesst. Weil die Antwortform juenger ist als dieser Code, wird sie
    hier NICHT vorausgesetzt, sondern abgetastet. Passt nichts, gilt der Wert
    als nicht vorhanden - geraten wird nichts.
    """
    liste = None
    if isinstance(antwort, list):
        liste = antwort
    elif isinstance(antwort, dict):
        for schluessel in ("transactions", "txs", "cluster", "entries", "chunk"):
            if isinstance(antwort.get(schluessel), list):
                liste = antwort[schluessel]
                break
    if not liste:
        return None

    eintraege = []
    for e in liste:
        if isinstance(e, str):
            eintraege.append({"txid": e, "sat": None, "vsize": None, "chunk": None})
            continue
        if not isinstance(e, dict):
            continue
        gebuehr = e.get("fee")
        if gebuehr is None and isinstance(e.get("fees"), dict):
            gebuehr = e["fees"].get("base")
        stueck = e.get("chunk")
        if stueck is None:
            stueck = e.get("chunk_index")
        eintraege.append({
            "txid": e.get("txid") or e.get("wtxid"),
            "sat": _sat(gebuehr),
            "vsize": e.get("vsize") or e.get("size"),
            "chunk": stueck,
        })
    if not eintraege:
        return None

    meins = next((e for e in eintraege if e["txid"] == txid), None)
    if meins is not None and meins["chunk"] is not None:
        gruppe = [e for e in eintraege if e["chunk"] == meins["chunk"]]
    else:
        # Ohne Chunk-Angabe ist die Einheit unbekannt; dann berichten wir nur
        # die Groesse des Clusters und behaupten keine Einbaurate.
        gruppe = None

    ergebnis = {"cluster_gross": len(eintraege), "chunk_gross": None,
                "chunk_vsize": None, "chunk_sat": None, "chunk_vb": None}
    if gruppe:
        ergebnis["chunk_gross"] = len(gruppe)
        vsizes = [e["vsize"] for e in gruppe if e["vsize"]]
        sats = [e["sat"] for e in gruppe if e["sat"] is not None]
        if len(vsizes) == len(gruppe) and len(sats) == len(gruppe):
            ergebnis["chunk_vsize"] = sum(vsizes)
            ergebnis["chunk_sat"] = sum(sats)
            if ergebnis["chunk_vsize"]:
                ergebnis["chunk_vb"] = ergebnis["chunk_sat"] / ergebnis["chunk_vsize"]
    return ergebnis


async def _ausgaben_im_mempool(tor, txid, anzahl):
    """Welche Ausgaenge werden gerade in einer UNBESTAETIGTEN Transaktion ausgegeben?

    gettxspendingprevout durchsucht ausschliesslich den Mempool. Fuer eine
    unbestaetigte Transaktion ist das die vollstaendige Antwort - ihre
    Ausgaenge koennen nirgends sonst ausgegeben sein. Fuer eine bestaetigte
    ist es nur die halbe: die andere Haelfte holt _ausgaben_im_index().
    """
    if not anzahl or anzahl > MAX_AUSGAENGE_PREVOUT:
        return {}
    antwort = await _sicher(tor, "gettxspendingprevout",
                            [{"txid": txid, "vout": n} for n in range(anzahl)])
    raus = {}
    for e in antwort or []:
        if isinstance(e, dict) and e.get("spendingtxid"):
            raus[e.get("vout")] = e["spendingtxid"]
    return raus


async def _ausgaben_im_index(ausgaenge, txid, blockhoehe, zeitlimit=6.0):
    """Welche Ausgaenge sind in einem BLOCK schon wieder ausgegeben?

    Der Electrum-Index weiss es: liegt (txid, n) nicht mehr in listunspent,
    ist der Ausgang weg. Vorbedingung, ohne die hier NICHTS behauptet wird:
    der Index muss den Block dieser Transaktion bereits kennen. Sonst faende
    er sie nur deshalb nicht, weil er hinterherhinkt - und wir wuerden
    "ausgegeben" luegen. Fehlt die Vorbedingung, bleibt der Stand offen.
    """
    host, port = elektrum.ziel()
    if not host or not port or blockhoehe is None:
        return {}
    ziel_skripte = {}
    for a in ausgaenge:
        if a["unausgebbar"] or not a["skript_hex"]:
            continue
        kennung = _scripthash(a["skript_hex"])
        if kennung:
            ziel_skripte.setdefault(kennung, []).append(a["n"])
    if not ziel_skripte or len(ziel_skripte) > MAX_SKRIPTE:
        return {}

    raus = {}
    try:
        async with elektrum.Verbindung(host, port, zeitlimit) as v:
            kopf = await v.frage("blockchain.headers.subscribe")
            index_hoehe = (kopf or {}).get("height")
            if not isinstance(index_hoehe, int) or index_hoehe < blockhoehe:
                return {}
            for kennung, nummern in ziel_skripte.items():
                offen = await v.frage("blockchain.scripthash.listunspent", [kennung])
                paare = {(e.get("tx_hash"), e.get("tx_pos")) for e in (offen or [])}
                for n in nummern:
                    raus[n] = (txid, n) not in paare
    except (OSError, asyncio.TimeoutError, ValueError, TypeError,
            elektrum.ElektrumFehler):
        # Was schon beantwortet ist, bleibt beantwortet; der Rest bleibt offen.
        return raus
    return raus


# --------------------------------------------------------------- Hauptzugriff
async def transaktion(tor, txid, jetzt=None):
    """Alles zu einer Transaktion, jeder Wert einzeln abgesichert.

    Faellt ein Aufruf aus, fehlt genau dieser Wert (None) - die Seite zeigt
    dort einen Strich und bleibt im Uebrigen stehen.
    """
    jetzt = int(time.time()) if jetzt is None else int(jetzt)
    eingabe = (txid or "").strip()
    txid = eingabe.lower()
    if not _hex64(txid):
        return {"gefunden": False, "grund": "keine_txid",
                "eingabe": eingabe[:80], "txid": None, "jetzt": jetzt}

    # Stufe 2 liefert zusaetzlich die Vorgaenger-Ausgaenge (Betrag, Skript) und
    # die Gebuehr - beides nur, wenn der Knoten die Undo-Daten des Blocks noch
    # hat. Ein beschnittener Knoten hat sie nicht; das kostet uns die
    # Eingangsbetraege, nicht die Seite.
    roh, fehler = await _versuch(tor, "getrawtransaction", txid, 2)
    if roh is None:
        # Zweiter Anlauf mit der kleinen Stufe: aeltere Knoten kennen die
        # Stufe 2 nicht. Erst wenn auch das scheitert, gibt es nichts zu zeigen.
        roh, fehler2 = await _versuch(tor, "getrawtransaction", txid, True)
        if roh is None:
            return {"gefunden": False, "grund": _grund(fehler2 or fehler),
                    "eingabe": eingabe[:80], "txid": txid, "jetzt": jetzt}

    vins = roh.get("vin") or []
    vouts = roh.get("vout") or []
    coinbase = bool(vins) and "coinbase" in (vins[0] or {})
    bestaetigungen = roh.get("confirmations")
    blockhash = roh.get("blockhash")
    bestaetigt = bool(bestaetigungen and bestaetigungen > 0)

    # ---- Eingaenge
    eingaenge, eingang_sat, prevouts_vollstaendig = [], 0, True
    for v in vins:
        vor = v.get("prevout") or {}
        skript = vor.get("scriptPubKey") or {}
        betrag = _sat(vor.get("value"))
        if betrag is None and not coinbase:
            prevouts_vollstaendig = False
        else:
            eingang_sat += betrag or 0
        sequenz = v.get("sequence")
        eingaenge.append({
            "coinbase": "coinbase" in v,
            "txid": v.get("txid"),
            "vout": v.get("vout"),
            "sequenz": sequenz,
            "rbf": isinstance(sequenz, int) and sequenz < SEQ_RBF,
            "final": sequenz == SEQ_FINAL,
            "betrag_sat": betrag,
            "adresse": skript.get("address") or (skript.get("addresses") or [None])[0],
            "art": _typ_name(skript.get("type")) if skript.get("type") else None,
            "skript_hex": skript.get("hex"),
            "zeuge": bool(v.get("txinwitness")),
            "text": _lesbar(bytes.fromhex(v["coinbase"]))
                    if v.get("coinbase") and _hex_ok(v["coinbase"]) else None,
        })
    if coinbase:
        eingang_sat, prevouts_vollstaendig = None, True

    # ---- Ausgaenge
    ausgaenge, ausgang_sat = [], 0
    for o in vouts:
        skript = o.get("scriptPubKey") or {}
        typ = skript.get("type")
        betrag = _sat(o.get("value")) or 0
        ausgang_sat += betrag
        daten_bytes, daten_text = (None, None)
        if typ == "nulldata":
            daten_bytes, daten_text = _opreturn(skript.get("hex"))
        ausgaenge.append({
            "n": o.get("n"),
            "betrag_sat": betrag,
            "adresse": skript.get("address") or (skript.get("addresses") or [None])[0],
            "art": _typ_name(typ) if typ else None,
            "skript_hex": skript.get("hex"),
            # OP_RETURN und Skripte ohne Ausgabeweg tauchen nie in einer
            # Unspent-Liste auf. Sie als "ausgegeben" zu zaehlen waere falsch.
            "unausgebbar": typ == "nulldata",
            "daten_bytes": daten_bytes,
            "daten_text": daten_text,
            "status": "unbekannt",
            "ausgegeben_von": None,
        })

    # ---- Nebenlaeufig alles, was die Einordnung braucht
    kette, kopf, eintrag, cluster, im_mempool = await asyncio.gather(
        _sicher(tor, "getblockchaininfo"),
        _sicher(tor, "getblockheader", blockhash) if blockhash else _nichts(),
        _sicher(tor, "getmempoolentry", txid) if not bestaetigt else _nichts(),
        _sicher(tor, "getmempoolcluster", txid) if not bestaetigt else _nichts(),
        _ausgaben_im_mempool(tor, txid, len(ausgaenge)),
    )
    hoehe = (kette or {}).get("blocks")
    blockhoehe = (kopf or {}).get("height")
    blockzeit = roh.get("blocktime") or (kopf or {}).get("time")

    # ---- Groessen und Gewicht
    groesse = roh.get("size")
    vgroesse = roh.get("vsize")
    gewicht = roh.get("weight")
    # Gewicht = 3 * Basisgroesse + Gesamtgroesse, also Basis = (Gewicht - Groesse)/3.
    # Ohne Zeugen faellt das auf die Groesse selbst zurueck - die Formel gilt immer.
    grundgroesse = ((gewicht - groesse) // 3) if (gewicht and groesse) else None
    zeugen_bytes = (groesse - grundgroesse) if (groesse and grundgroesse) else 0
    # Zeugendaten zaehlen nur ein Viertel. Gespart wird also drei Viertel davon.
    rabatt_vbyte = int(zeugen_bytes * 3 / 4) if zeugen_bytes else None
    segwit = bool(zeugen_bytes) or any(e["zeuge"] for e in eingaenge)

    # ---- Gebuehr: bevorzugt selbst gerechnet, ganzzahlig
    gebuehr_sat = None
    if coinbase:
        gebuehr_sat = None
    elif prevouts_vollstaendig and eingang_sat is not None:
        gebuehr_sat = eingang_sat - ausgang_sat
    elif roh.get("fee") is not None:
        gebuehr_sat = _sat(roh.get("fee"))
    elif isinstance(eintrag, dict):
        gebuehr_sat = _sat((eintrag.get("fees") or {}).get("base"))
    gebuehr_vb = (gebuehr_sat / vgroesse) if (gebuehr_sat is not None and vgroesse) else None

    # ---- Mempool-Umfeld
    mempool = None
    if isinstance(eintrag, dict):
        gebuehren = eintrag.get("fees") or {}
        ahnen_sat = _sat(gebuehren.get("ancestor"))
        nach_sat = _sat(gebuehren.get("descendant"))
        ahnen_vsize = eintrag.get("ancestorsize")
        nach_vsize = eintrag.get("descendantsize")
        mempool = {
            "seit": eintrag.get("time"),
            "alter_s": (jetzt - eintrag["time"]) if eintrag.get("time") else None,
            "ahnen": eintrag.get("ancestorcount"),
            "ahnen_sat": ahnen_sat,
            "ahnen_vsize": ahnen_vsize,
            "ahnen_vb": (ahnen_sat / ahnen_vsize)
                        if (ahnen_sat is not None and ahnen_vsize) else None,
            "nachfahren": eintrag.get("descendantcount"),
            "nachfahren_sat": nach_sat,
            "nachfahren_vsize": nach_vsize,
            "nachfahren_vb": (nach_sat / nach_vsize)
                             if (nach_sat is not None and nach_vsize) else None,
            "haengt_an": eintrag.get("depends") or [],
            "ausgegeben_von": eintrag.get("spentby") or [],
            # bip125-replaceable steht auch dann auf true, wenn nicht sie
            # selbst, sondern ein unbestaetigter Vorgaenger signalisiert.
            "ersetzbar": eintrag.get("bip125-replaceable"),
            "unverbreitet": eintrag.get("unbroadcast"),
        }

    # ---- Ausgabestand je Ausgang zusammensetzen
    if bestaetigt:
        im_index = await _ausgaben_im_index(ausgaenge, txid, blockhoehe)
    else:
        # Eine unbestaetigte Transaktion kann nur im Mempool ausgegeben werden -
        # der Index hat zu ihren Ausgaengen nichts beizutragen.
        im_index = {}
    for a in ausgaenge:
        if a["unausgebbar"]:
            a["status"] = "daten"
            continue
        if a["n"] in im_mempool:
            a["status"] = "ausgegeben_offen"
            a["ausgegeben_von"] = im_mempool[a["n"]]
        elif a["n"] in im_index:
            a["status"] = "ausgegeben" if im_index[a["n"]] else "offen"
        elif not bestaetigt:
            # Hier ist die Mempool-Auskunft vollstaendig: nicht gefunden heisst
            # sicher unberuehrt.
            a["status"] = "offen"

    # ---- Einordnung im eigenen Block bzw. gegen den Andrang
    blockstats = None
    schaetzungen, letzte_min = {}, None
    if bestaetigt and blockhoehe is not None:
        blockstats = await _sicher(
            tor, "getblockstats", blockhoehe,
            ["feerate_percentiles", "minfeerate", "maxfeerate", "avgfeerate",
             "totalfee", "subsidy", "txs"])
    elif not bestaetigt:
        schaetzungen, letzte_min = await asyncio.gather(
            _schaetzungen(tor), _letzte_bloecke(tor, hoehe))

    perzentile = (blockstats or {}).get("feerate_percentiles")
    perzentile = perzentile if isinstance(perzentile, list) and len(perzentile) == 5 else None
    klasse = None
    if perzentile and gebuehr_vb is not None:
        p10, p25, p50, p75, p90 = [float(x) for x in perzentile]
        if gebuehr_vb < p10:
            klasse = "unter10"
        elif gebuehr_vb >= p90:
            klasse = "ueber90"
        elif gebuehr_vb >= p75:
            klasse = "ueber75"
        elif gebuehr_vb >= p50:
            klasse = "ueber50"
        else:
            klasse = "unter50"

    schaetzung_bloecke = None
    if gebuehr_vb is not None and schaetzungen:
        passend = [z for z, r in schaetzungen.items() if r <= gebuehr_vb]
        schaetzung_bloecke = min(passend) if passend else None

    # ---- Sperrzeit
    sperrzeit = roh.get("locktime") or 0
    sperrzeit_art = None
    if sperrzeit:
        sperrzeit_art = "hoehe" if sperrzeit < SPERRZEIT_GRENZE else "zeit"
    # nLockTime wirkt nur, wenn mindestens ein Eingang NICHT final ist.
    sperrzeit_wirksam = bool(sperrzeit) and any(not e["final"] for e in eingaenge)
    # Als Datum nur, wenn es eines ist. UTC und nicht die Ortszeit des Servers:
    # der Container hat keine Zeitzone des Nutzers, eine erfundene waere falsch.
    sperrzeit_utc = (time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(sperrzeit))
                     if sperrzeit_art == "zeit" else None)
    antisniping = None
    if (sperrzeit_art == "hoehe" and sperrzeit_wirksam
            and blockhoehe is not None and 0 <= blockhoehe - sperrzeit <= 10):
        antisniping = blockhoehe - sperrzeit

    # ---- Wechselgeld-Verdacht (ausdruecklich nur ein Verdacht)
    wechselgeld = None
    eingangsarten = {e["art"] for e in eingaenge if e["art"]}
    if len(ausgaenge) == 2 and len(eingangsarten) == 1 and not coinbase:
        art = eingangsarten.pop()
        treffer = [a for a in ausgaenge if a["art"] == art]
        if len(treffer) == 1:
            wechselgeld = treffer[0]["n"]

    # ---- Adresswiederverwendung innerhalb derselben Transaktion
    eingangsskripte = {e["skript_hex"] for e in eingaenge if e["skript_hex"]}
    zurueck = [a["n"] for a in ausgaenge
               if a["skript_hex"] and a["skript_hex"] in eingangsskripte]

    # ---- Gebuehrenband fuer die Anzeige
    band = _band(gebuehr_vb, perzentile, schaetzungen)

    return {
        "gefunden": True,
        "grund": None,
        "jetzt": jetzt,
        "txid": roh.get("txid") or txid,
        "wtxid": roh.get("hash"),
        "version": roh.get("version"),
        "coinbase": coinbase,
        "bestaetigt": bestaetigt,
        "bestaetigungen": bestaetigungen,
        "verwaist": bool(blockhash) and not bestaetigt and mempool is None,
        "blockhash": blockhash,
        "blockhoehe": blockhoehe,
        "blockzeit": blockzeit,
        "block_alter": (jetzt - blockzeit) if blockzeit else None,
        "hoehe": hoehe,
        "reif_in": max(0, REIFE - (bestaetigungen or 0)) if coinbase else None,
        "groesse": groesse,
        "vgroesse": vgroesse,
        "gewicht": gewicht,
        "grundgroesse": grundgroesse,
        "segwit": segwit,
        "zeugen_bytes": zeugen_bytes or None,
        "rabatt_vbyte": rabatt_vbyte,
        "block_anteil": (vgroesse * 100.0 / BLOCK_VBYTE) if vgroesse else None,
        "rbf": any(e["rbf"] for e in eingaenge),
        "sperrzeit": sperrzeit,
        "sperrzeit_art": sperrzeit_art,
        "sperrzeit_utc": sperrzeit_utc,
        "sperrzeit_wirksam": sperrzeit_wirksam,
        "antisniping": antisniping,
        "eingaenge": eingaenge,
        "ausgaenge": ausgaenge,
        "eingang_sat": eingang_sat if prevouts_vollstaendig else None,
        "ausgang_sat": ausgang_sat,
        "prevouts_vollstaendig": prevouts_vollstaendig,
        "gebuehr_sat": gebuehr_sat,
        "gebuehr_vb": gebuehr_vb,
        "gebuehr_anteil": (gebuehr_sat * 100.0 / ausgang_sat)
                          if (gebuehr_sat and ausgang_sat) else None,
        "mempool": mempool,
        "chunk": _chunk_lesen(cluster, txid),
        "perzentile": perzentile,
        "perzentil_klasse": klasse,
        "blockstats": blockstats,
        "schaetzungen": schaetzungen,
        "schaetzung_bloecke": schaetzung_bloecke,
        "letzte_min_vb": letzte_min,
        "mempool_min_vb": _mempool_min_vb(kette),
        "wechselgeld": wechselgeld,
        "zurueck_an_eingang": zurueck,
        "band": band,
    }


async def _nichts():
    """Platzhalter, damit asyncio.gather() eine feste Stellenzahl behaelt."""
    return None


def _hex_ok(wert):
    try:
        bytes.fromhex(wert)
        return True
    except (ValueError, TypeError):
        return False


def _mempool_min_vb(kette):
    """mempoolminfee steht in BTC/kvB. Erst hier, kurz vor der Anzeige, geteilt."""
    wert = (kette or {}).get("mempoolminfee")
    if wert is None:
        return None
    try:
        return float(wert) * 100000.0
    except (TypeError, ValueError):
        return None


def _band(rate, perzentile, schaetzungen):
    """Wo diese Gebuehrenrate zwischen zwei sprechenden Marken liegt.

    Bestaetigt: zwischen dem 10. und dem 90. Perzentil ihres Blocks - die
    Frage lautet dann "hat sie im Vergleich zu ihren Nachbarn viel gezahlt".
    Unbestaetigt: zwischen der billigsten und der teuersten Schaetzung - die
    Frage lautet dann "wie weit vorn steht sie in der Warteschlange".
    """
    if rate is None:
        return None
    if perzentile:
        unten, oben, mitte, quelle = float(perzentile[0]), float(perzentile[4]), \
            float(perzentile[2]), "block"
    elif schaetzungen:
        werte = sorted(schaetzungen.values())
        unten, oben, mitte, quelle = werte[0], werte[-1], werte[len(werte) // 2], "andrang"
    else:
        return None
    if oben <= unten:
        return None
    anteil = (rate - unten) / (oben - unten)
    return {
        "quelle": quelle,
        "unten": unten,
        "oben": oben,
        "mitte": mitte,
        "rate": rate,
        "pos": max(0.0, min(1.0, anteil)) * 100.0,
        "mitte_pos": max(0.0, min(1.0, (mitte - unten) / (oben - unten))) * 100.0,
        "ueber": rate > oben,
        "unter": rate < unten,
    }


# --------------------------------------------------------------- Befunde
def befunde(d, t):
    """Die Saetze ueber der Tabelle. Reihenfolge = Wichtigkeit.

    Jeder Satz haengt an einem Wert, der wirklich da ist. Fehlt der Wert,
    fehlt der Satz - erfunden wird nichts, und eine fehlende Zahl leert
    hoechstens ihre eigene Zeile.
    """
    if not d.get("gefunden"):
        return []
    raus = []

    def sag(schluessel, art="info", **werte):
        raus.append({"text": t.t(schluessel, **werte), "art": art})

    z = t.zahl

    # --- Was fuer eine Transaktion ist das ueberhaupt
    if d["coinbase"]:
        sag("tx.f.coinbase", art="gut", h=z(d["blockhoehe"]))
        if d["reif_in"]:
            sag("tx.f.coinbase_reif", art="warn", n=z(d["reif_in"]))
        tag = next((e["text"] for e in d["eingaenge"] if e.get("text")), None)
        if tag:
            sag("tx.f.coinbase_tag", text=tag)
    elif d["verwaist"]:
        sag("tx.f.verwaist", art="warn")
    elif d["bestaetigt"]:
        if d["bestaetigungen"] == 1:
            sag("tx.f.bestaetigt_1", art="gut", h=z(d["blockhoehe"]))
        else:
            sag("tx.f.bestaetigt", art="gut",
                n=z(d["bestaetigungen"]), h=z(d["blockhoehe"]))
    elif d["mempool"] and d["mempool"]["alter_s"] is not None:
        sag("tx.f.wartet", art="warn", dauer=spanne(d["mempool"]["alter_s"], t))
    else:
        sag("tx.f.offen", art="warn")

    # --- Darf sie ersetzt werden
    if not d["bestaetigt"] and not d["coinbase"]:
        if d["rbf"]:
            sag("tx.f.rbf_ja", art="warn")
        elif d["mempool"] and d["mempool"].get("ersetzbar"):
            sag("tx.f.rbf_ererbt", art="warn")
        else:
            sag("tx.f.rbf_nein")

    # --- Reicht die Gebuehr
    if not d["bestaetigt"] and d["gebuehr_vb"] is not None:
        if d["letzte_min_vb"] is not None and d["gebuehr_vb"] < d["letzte_min_vb"]:
            sag("tx.f.zu_billig", art="warn", rate=z(d["gebuehr_vb"], 2),
                n=z(VERGLEICHSBLOECKE), min=z(d["letzte_min_vb"], 2))
        elif d["schaetzung_bloecke"] == 1:
            sag("tx.f.reicht_naechster", art="gut")
        elif d["schaetzung_bloecke"]:
            sag("tx.f.reicht", art="gut", n=z(d["schaetzung_bloecke"]))
        elif d["schaetzungen"]:
            sag("tx.f.reicht_nicht", art="warn",
                n=z(max(d["schaetzungen"])), rate=z(min(d["schaetzungen"].values()), 2))
        if (d["mempool_min_vb"] is not None
                and d["gebuehr_vb"] < d["mempool_min_vb"] * 1.2):
            sag("tx.f.raus_geflogen", art="warn", min=z(d["mempool_min_vb"], 2))

    # --- Die Kette, in der sie haengt
    m = d["mempool"]
    if m:
        if m["ahnen"] and m["ahnen"] > 1 and m["ahnen_vb"] is not None:
            vorher = m["ahnen"] - 1
            schluessel = "tx.f.ahnen_1" if vorher == 1 else "tx.f.ahnen"
            sag(schluessel, art="warn", n=z(vorher), rate=z(m["ahnen_vb"], 2))
        if m["nachfahren"] and m["nachfahren"] > 1 and m["nachfahren_vb"] is not None:
            danach = m["nachfahren"] - 1
            schluessel = "tx.f.nachfahren_1" if danach == 1 else "tx.f.nachfahren"
            art = "gut" if (d["gebuehr_vb"] is not None
                            and m["nachfahren_vb"] > d["gebuehr_vb"]) else "info"
            sag(schluessel, art=art, n=z(danach), rate=z(m["nachfahren_vb"], 2))
        if m.get("unverbreitet"):
            sag("tx.f.unverbreitet", art="warn")

    c = d["chunk"]
    if c and c.get("chunk_gross") and c["chunk_gross"] > 1 and c.get("chunk_vb") is not None:
        sag("tx.f.chunk", n=z(c["chunk_gross"]), rate=z(c["chunk_vb"], 2))
    elif c and c.get("cluster_gross") and c["cluster_gross"] > 1:
        sag("tx.f.cluster", n=z(c["cluster_gross"]))

    # --- Einordnung im eigenen Block
    if d["perzentil_klasse"] == "ueber90":
        sag("tx.f.perzentil_hoch", rate=z(d["gebuehr_vb"], 2))
    elif d["perzentil_klasse"] == "unter10":
        sag("tx.f.perzentil_niedrig", rate=z(d["gebuehr_vb"], 2))
    elif d["perzentil_klasse"] and d["perzentile"]:
        sag("tx.f.perzentil_mitte", rate=z(d["gebuehr_vb"], 2),
            median=z(float(d["perzentile"][2]), 2))

    # --- Was aus den Ausgaengen wurde
    zaehlbar = [a for a in d["ausgaenge"] if a["status"] != "daten"]
    weg = [a for a in zaehlbar if a["status"] == "ausgegeben"]
    unterwegs = [a for a in zaehlbar if a["status"] == "ausgegeben_offen"]
    offen = [a for a in zaehlbar if a["status"] == "offen"]
    if zaehlbar and len(weg) + len(unterwegs) + len(offen) == len(zaehlbar):
        if len(weg) == len(zaehlbar) and len(zaehlbar) > 1:
            sag("tx.f.alle_weg", n=z(len(zaehlbar)))
        elif len(weg) == 1 and len(zaehlbar) > 1:
            sag("tx.f.teils_weg_1", gesamt=z(len(zaehlbar)))
        elif weg:
            sag("tx.f.teils_weg", n=z(len(weg)), gesamt=z(len(zaehlbar)))
        elif len(offen) == len(zaehlbar) and not unterwegs:
            schluessel = "tx.f.alle_offen_1" if len(zaehlbar) == 1 else "tx.f.alle_offen"
            sag(schluessel, art="gut", n=z(len(zaehlbar)))
    if unterwegs:
        schluessel = "tx.f.unterwegs_1" if len(unterwegs) == 1 else "tx.f.unterwegs"
        sag(schluessel, art="warn", n=z(len(unterwegs)))

    # --- Form und Bauart
    if d["segwit"] and d["rabatt_vbyte"]:
        sag("tx.f.segwit", art="gut", b=z(d["zeugen_bytes"]),
            v=z(d["zeugen_bytes"] / 4.0, 0), s=z(d["rabatt_vbyte"]))
    elif not d["segwit"] and not d["coinbase"]:
        sag("tx.f.kein_segwit")

    if not d["coinbase"]:
        n_ein, n_aus = len(d["eingaenge"]), len(d["ausgaenge"])
        if n_aus == 1 and n_ein >= 5:
            sag("tx.f.zusammenlegung", n=z(n_ein))
        elif n_ein <= 2 and n_aus >= 10:
            sag("tx.f.verteilung", n=z(n_aus))

    if d["zurueck_an_eingang"]:
        sag("tx.f.wiederverwendung", art="warn")
    elif d["wechselgeld"] is not None:
        sag("tx.f.wechselgeld", n=z(d["wechselgeld"]))

    daten = next((a for a in d["ausgaenge"] if a["status"] == "daten"), None)
    if daten and daten["daten_bytes"]:
        if daten["daten_text"]:
            sag("tx.f.opreturn_text", b=z(daten["daten_bytes"]), text=daten["daten_text"])
        else:
            sag("tx.f.opreturn", b=z(daten["daten_bytes"]))

    if d["antisniping"] is not None:
        # Der Abstand selbst waere eine nichtssagende Zahl (meist 1). Interessant
        # ist die Sperrzeit als Hoehe: sie verraet, wann die Wallet signiert hat.
        sag("tx.f.antisniping", h=z(d["sperrzeit"]))
    elif d["sperrzeit"] and d["sperrzeit_wirksam"] and d["sperrzeit_art"] == "hoehe":
        sag("tx.f.sperrzeit_hoehe", h=z(d["sperrzeit"]))
    elif d["sperrzeit"] and not d["sperrzeit_wirksam"]:
        sag("tx.f.sperrzeit_folgenlos")

    if d["block_anteil"] and d["block_anteil"] >= 1.0:
        sag("tx.f.gross", v=z(d["vgroesse"]), p=z(d["block_anteil"], 1))
    if d["gebuehr_anteil"] and d["gebuehr_anteil"] >= 1.0:
        sag("tx.f.gebuehr_anteil", art="warn", p=z(d["gebuehr_anteil"], 1))

    if not d["prevouts_vollstaendig"]:
        sag("tx.f.keine_prevouts", art="warn")

    return raus
