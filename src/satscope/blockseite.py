"""Ein Block, in ganzen Saetzen erklaert.

Die Entwurfsidee des Projekts ist die Einordnung, nicht die nackte Zahl. Dieses
Modul erhebt deshalb nicht nur die Felder eines Blocks, sondern setzt sie ins
Verhaeltnis: zur Konsensgrenze (Fuellung), zur Belohnungsepoche (Halbierung),
zum Vorgaenger (Abstand) und zu den letzten Bloecken (Vergleichsfenster).

Was hier bewusst NICHT passiert: Text. Dieses Modul liefert Rohwerte und
Einordnungs-Merkmale ("voll", "tiefster", "rueckwaerts"); die Saetze stehen im
Textkatalog und werden in block.html zusammengesetzt. Andernfalls waere die
Zweisprachigkeit an genau der Stelle nicht mehr zu halten, an der sie am
meisten wehtut - in ganzen Saetzen weicht die Wortstellung ab.

Jeder Wert ist EINZELN abgesichert (wie knoten._sicher): faellt getblockstats
aus, fehlen die Gebuehrenbefunde und der Rest der Seite steht trotzdem. Ein
beschnittener Knoten ("pruned") ist genau dieser Fall und kein Fehler.

KOSTEN am Knoten (gemessen 15.08.2026, siehe rpc.py):
    getblockchaininfo    9 ms   einmal: Erreichbarkeit und Kettenspitze
    getblockhash        <1 ms   nur wenn eine Hoehe eingegeben wurde
    getblockheader      <5 ms   einmal Block, einmal Vorgaenger
    getblockstats    19-30 ms   einmal fuer den Block selbst
    getblockstats    19-30 ms   x12 fuer die Einordnung  <-- der teure Teil
Nacheinander waeren das rund 410 ms. Das Tor laesst vier Aufrufe gleichzeitig
durch, die zwoelf Vergleichsaufrufe brauchen also drei Runden statt zwoelf; die
Seite steht nach ungefaehr 150 ms. Mehr als zwoelf Vorgaenger holen wir
absichtlich nicht: jeder weitere Aufruf belastet einen Knoten, auf dem echtes
Geld liegt, und die Aussage wird davon kaum besser.
"""
import asyncio
import time

from .rpc import RpcFehler
from .sprache import STANDARD

# Konsensgrenze: 4.000.000 Gewichtseinheiten je Block. Nicht die 1 MB, die alle
# zitieren - die Fuellung an der Groesse zu messen waere seit SegWit falsch.
GEWICHTSGRENZE = 4000000
# Alle 210.000 Bloecke halbiert sich die Belohnung.
EPOCHENLAENGE = 210000
# Zielabstand zwischen zwei Bloecken (Sekunden).
ZIELABSTAND = 600
# Ab hier gilt ein Block gemeinhin als nicht mehr umkehrbar.
ENDGUELTIG_AB = 6

# Hoechstens so viele zusaetzliche getblockstats fuer die Einordnung. Zwoelf
# Bloecke sind rund zwei Stunden - genug, um "ungewoehnlich" von "normal" zu
# trennen, und wenig genug, dass der Knoten es nicht merkt.
VERGLEICHSFENSTER = 12

# Unterhalb von so vielen Vergleichswerten sagen wir gar nichts. Bei zwei
# Nachbarn ist "der niedrigste" eine Behauptung, keine Beobachtung.
MINDESTVERGLEICH = 3

# Nur diese Felder holen wir fuer die Vergleichsbloecke. Die Auswahl ist keine
# Kosmetik: bitcoind rechnet nur, wonach gefragt wird.
VERGLEICHSFELDER = ["height", "time", "feerate_percentiles", "minfeerate",
                    "total_weight", "txs", "totalfee"]

HEXZIFFERN = frozenset("0123456789abcdef")


# --------------------------------------------------------------- Werkzeug
async def _sicher(tor, methode, *argumente):
    """Ruft auf und liefert None statt zu werfen - wie knoten._sicher.

    Absichtlich dupliziert statt importiert: der Strich statt einer erfundenen
    Zahl ist die wichtigste Regel dieses Projekts, und sie soll in jedem Modul
    sichtbar dastehen, nicht hinter einem Import verschwinden.
    """
    try:
        return await tor.ruf(methode, *argumente)
    except (RpcFehler, OSError, asyncio.TimeoutError):
        return None


def _anteil(teil, ganz):
    """Prozent - oder None, wenn eine Seite fehlt. Nie durch Null."""
    if teil is None or not ganz:
        return None
    return teil / ganz * 100.0


def _verhaeltnis(oben, unten):
    """Das Wievielfache. None statt einer Division durch Null."""
    if oben is None or not unten:
        return None
    return oben / unten


def btc_text(sat, sprache=STANDARD):
    """Satoshi als BTC mit acht Stellen, sprachrichtig.

    Bewusst NICHT ueber Fliesskomma - 0,1 + 0,2 ist dort nicht 0,3, und bei
    Geldbetraegen faellt so etwas irgendwann auf. Gleiche Rechnung wie in
    web.py; sie steht hier nochmals, damit die Blockseite auch dann zaehlbar
    bleibt, wenn die Route den Helfer nicht mitgibt.
    """
    if sat is None:
        return "–"
    ganz, rest = divmod(int(sat), 100000000)
    s = "%d.%08d" % (ganz, rest)
    return s.replace(".", ",") if sprache == "de" else s


def uhrzeit(stempel):
    """Zeitstempel als UTC. Keine Ortszeit: der Server kennt die des Browsers
    nicht, und eine falsche Zeitzone ist schlimmer als eine fremde."""
    if not stempel:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(int(stempel))) + " UTC"


def kennung_zerlegen(kennung):
    """('hoehe', int) oder ('hash', str) oder (None, None).

    Streng auf ASCII geprueft: str.isdigit() ist auch fuer arabisch-indische
    Ziffern wahr, und was wir nicht lesen koennen, schicken wir nicht an
    bitcoind weiter.
    """
    k = (kennung or "").strip()
    if k and k.isascii() and k.isdigit() and len(k) <= 9:
        return "hoehe", int(k)
    if len(k) == 64 and set(k.lower()) <= HEXZIFFERN:
        return "hash", k.lower()
    return None, None


def _arbeit(schwierigkeit):
    """Erwartete Hashversuche fuer einen Treffer, in lesbarer Einheit.

    Schwierigkeit * 2^32 ist Definition, keine Schaetzung. Die Einheit waechst
    mit: EH = 10^18, ZH = 10^21, YH = 10^24. Ausgeschriebene Zahlwoerter waeren
    hier eine Falle - "Trillion" meint auf Deutsch 10^18, auf Englisch 10^12.
    """
    if not schwierigkeit:
        return None, None
    versuche = float(schwierigkeit) * 4294967296.0
    for teiler, name in ((1e24, "YH"), (1e21, "ZH"), (1e18, "EH"), (1e15, "PH")):
        if versuche >= teiler:
            return versuche / teiler, name
    return versuche / 1e12, "TH"


def _fuehrende_nullen(hash_hex):
    """Wie viele Nullziffern der Hash vorne hat - sichtbar gewordene Arbeit."""
    if not hash_hex:
        return None
    return len(hash_hex) - len(hash_hex.lstrip("0"))


# --------------------------------------------------------------- Einordnung
def _kennzahlen(s):
    """Die fuenf Groessen, ueber die wir Bloecke vergleichen."""
    p = s.get("feerate_percentiles") or []
    gewicht = s.get("total_weight")
    return {
        "medianrate": p[2] if len(p) >= 5 else None,
        "minrate": s.get("minfeerate"),
        "fuellung": _anteil(gewicht, GEWICHTSGRENZE) if gewicht is not None else None,
        "txs": s.get("txs"),
        "gebuehren": s.get("totalfee"),
    }


def _urteil(wert, reihe):
    """Wo steht `wert` in der Reihe seiner Vorgaenger?

    Liefert None, wenn zu wenige Vergleichswerte da sind - lieber gar keine
    Einordnung als eine, die auf zwei Nachbarn beruht.
    """
    reihe = [x for x in reihe if x is not None]
    if wert is None or len(reihe) < MINDESTVERGLEICH:
        return None
    hoeher = sum(1 for x in reihe if x > wert)
    tiefer = sum(1 for x in reihe if x < wert)
    geordnet = sorted(reihe)
    mitte = geordnet[len(geordnet) // 2]
    abweichung = _anteil(wert - mitte, abs(mitte)) if mitte else None

    if hoeher == 0 and tiefer > 0:
        klasse = "hoechster"
    elif tiefer == 0 and hoeher > 0:
        klasse = "tiefster"
    elif abweichung is None or abs(abweichung) < 10.0:
        # Zehn Prozent Totzone: darunter ist der Unterschied Rauschen, und
        # "leicht ueber dem Mittel" ist keine Aussage, sondern Geraeusch.
        klasse = "mittig"
    elif abweichung < 0:
        klasse = "unter"
    else:
        klasse = "ueber"
    return {"klasse": klasse, "mitte": mitte, "abweichung": abweichung,
            "n": len(reihe)}


async def _vergleichsreihe(tor, hoehe):
    """getblockstats fuer bis zu VERGLEICHSFENSTER Vorgaenger.

    Der teuerste Teil der Seite (12 x 19-30 ms, gedeckelt auf vier
    gleichzeitige). Ausfaelle einzelner Bloecke sind eingeplant: was fehlt,
    faellt aus der Reihe, das Urteil wird dadurch nur schwaecher, nie falsch.
    """
    hoehen = [h for h in range(hoehe - VERGLEICHSFENSTER, hoehe) if h >= 0]
    if not hoehen:
        return []
    roh = await asyncio.gather(*[
        _sicher(tor, "getblockstats", h, VERGLEICHSFELDER) for h in hoehen])
    return [s for s in roh if s]


# --------------------------------------------------------------- Balken
def _balken(perzentile):
    """Die Gebuehrenperzentile als Saeulenhoehen in Prozent.

    Massstab ist das 90. Perzentil, nicht die teuerste Transaktion: eine
    einzelne Notfall-Transaktion mit 400 sat/vB wuerde sonst alle anderen
    Saeulen auf einen Strich druecken, und gerade die Form der Verteilung ist
    hier die Aussage. Der Ausreisser steht stattdessen im Satz daneben.
    """
    if not perzentile or len(perzentile) < 5:
        return None
    p10, p25, p50, p75, p90 = perzentile[:5]
    massstab = p90 or max(perzentile) or 0
    if not massstab:
        return None
    reihe = (("p10", p10), ("p25", p25), ("p50", p50),
             ("p75", p75), ("p90", p90))
    return [{"name": n, "wert": w,
             "anteil": max(2.0, min(100.0, (w or 0) / massstab * 100.0))}
            for n, w in reihe]


# --------------------------------------------------------------- Hauptweg
def _leer(grund, eingabe, spitze=None):
    return {"gefunden": False, "grund": grund, "eingabe": eingabe,
            "spitze": spitze, "btc": lambda sat: btc_text(sat, STANDARD)}


async def blockdaten(tor, kennung, sprache=STANDARD):
    """Alles, was block.html braucht - oder ein sauberes "nicht gefunden".

    `kennung` darf eine Hoehe oder ein Hash sein; beides nimmt der Nutzer aus
    der einen Suchzeile, ohne wissen zu muessen, was er da eingibt.
    """
    art, wert = kennung_zerlegen(kennung)
    if art is None:
        return _leer("form", kennung)

    # Erst die Kettenspitze. Der Aufruf kostet 9 ms und kauft zwei Dinge, die
    # sonst nicht zu trennen waeren: einen nicht erreichbaren Knoten von einem
    # unbekannten Block - und eine Hoehe jenseits der Spitze koennen wir damit
    # abweisen, ohne bitcoind ueberhaupt zu fragen.
    kette = await _sicher(tor, "getblockchaininfo")
    if kette is None:
        return _leer("knoten", kennung)
    spitze = kette.get("blocks")

    if art == "hoehe":
        if spitze is not None and wert > spitze:
            return _leer("zukunft", kennung, spitze)
        blockhash = await _sicher(tor, "getblockhash", wert)
        if not blockhash:
            return _leer("unbekannt", kennung, spitze)
    else:
        blockhash = wert

    kopf = await _sicher(tor, "getblockheader", blockhash)
    if not kopf:
        return _leer("unbekannt", kennung, spitze)

    hoehe = kopf.get("height")
    bestaetigungen = kopf.get("confirmations")
    # -1 heisst: der Block liegt uns vor, gehoert aber NICHT zur besten Kette.
    # Das ist kein Fehler, sondern ein verwaister Block - und eine der wenigen
    # Gelegenheiten, das ueberhaupt zu sehen.
    verwaist = bestaetigungen is not None and bestaetigungen < 0

    # Stats ueber den HASH, nicht ueber die Hoehe: bei einem verwaisten Block
    # wuerde die Hoehe den Konkurrenten der besten Kette treffen.
    stats = await _sicher(tor, "getblockstats", blockhash)

    # Vergleich nur fuer Bloecke der besten Kette. Einen verwaisten Block gegen
    # die Hauptkette zu halten waere ein Vergleich von Aepfeln mit Birnen.
    reihe = [] if (verwaist or hoehe is None) else await _vergleichsreihe(tor, hoehe)

    vorgaenger_zeit = None
    if kopf.get("previousblockhash"):
        vorkopf = await _sicher(tor, "getblockheader", kopf["previousblockhash"])
        vorgaenger_zeit = (vorkopf or {}).get("time")

    return _zusammensetzen(kopf, stats, reihe, vorgaenger_zeit, spitze,
                           verwaist, sprache)


def _zusammensetzen(kopf, stats, reihe, vorgaenger_zeit, spitze, verwaist,
                    sprache):
    """Rohwerte zu Befunden verdichten. Reine Rechnung, kein Netz - damit
    genau dieser Teil ohne Knoten pruefbar ist."""
    s = stats or {}
    hoehe = kopf.get("height")
    zeit = kopf.get("time")
    blockhash = kopf.get("hash")

    # ---- Groesse und Fuellung
    gewicht = s.get("total_weight")
    groesse = s.get("total_size")
    fuellung = _anteil(gewicht, GEWICHTSGRENZE)
    if fuellung is None:
        fuellung_klasse = None
    elif fuellung >= 99.0:
        fuellung_klasse = "randvoll"
    elif fuellung >= 90.0:
        fuellung_klasse = "voll"
    elif fuellung >= 50.0:
        fuellung_klasse = "halb"
    else:
        fuellung_klasse = "leer"

    txs = s.get("txs")
    # txs zaehlt die Coinbase mit. Ein Block "mit einer Transaktion" enthaelt
    # also keine einzige Zahlung - das ist der Unterschied, den wir zeigen.
    zahlungen = (txs - 1) if txs else None

    # ---- Geld
    subsidy = s.get("subsidy")
    gebuehren = s.get("totalfee")
    lohn = (subsidy + gebuehren) if (subsidy is not None and gebuehren is not None) else None
    gebuehrenanteil = _anteil(gebuehren, lohn)
    # Was derselbe Block nach der naechsten Halbierung bedeuten wuerde. Reine
    # Arithmetik auf seinen eigenen Zahlen, keine Prognose - deshalb steht im
    # Satz auch ausdruecklich "haette".
    anteil_nach_halbierung = None
    if subsidy is not None and gebuehren is not None and (subsidy // 2 + gebuehren):
        anteil_nach_halbierung = _anteil(gebuehren, subsidy // 2 + gebuehren)

    epoche = (hoehe // EPOCHENLAENGE) if hoehe is not None else None
    halbierung = ((epoche + 1) * EPOCHENLAENGE) if epoche is not None else None
    bis_halbierung = (halbierung - hoehe) if halbierung is not None else None
    # Tage bei ZIELABSTAND je Block. Ausdruecklich eine Annahme, nicht eine
    # Messung; der Satz im Katalog nennt sie deshalb mit.
    tage_halbierung = (bis_halbierung * ZIELABSTAND / 86400.0) if bis_halbierung else None

    # ---- Gebuehrenverteilung
    perzentile = s.get("feerate_percentiles") or None
    medianrate = perzentile[2] if perzentile and len(perzentile) >= 5 else None
    p10 = perzentile[0] if perzentile and len(perzentile) >= 5 else None
    p90 = perzentile[4] if perzentile and len(perzentile) >= 5 else None
    minrate = s.get("minfeerate")
    maxrate = s.get("maxfeerate")
    spanne = _verhaeltnis(p90, p10)
    if spanne is None:
        spanne_klasse = None
    elif spanne < 1.5:
        spanne_klasse = "eng"
    elif spanne < 4.0:
        spanne_klasse = "normal"
    else:
        spanne_klasse = "weit"
    max_vielfaches = _verhaeltnis(maxrate, medianrate)

    # ---- Struktur
    swtxs = s.get("swtxs")
    segwit_txs = _anteil(swtxs, txs)
    segwit_bytes = _anteil(s.get("swtotal_size"), groesse)
    ein = s.get("ins")
    aus = s.get("outs")
    utxo = s.get("utxo_increase")
    if utxo is None:
        utxo_klasse = None
    elif utxo > 0:
        utxo_klasse = "wachstum"
    elif utxo < 0:
        utxo_klasse = "schrumpfung"
    else:
        utxo_klasse = "gleich"

    # ---- Zeit
    abstand = (zeit - vorgaenger_zeit) if (zeit and vorgaenger_zeit) else None
    if abstand is None:
        abstand_klasse = None
    elif abstand < 0:
        # Voellig regelkonform: ein Zeitstempel muss nur ueber der
        # Median-Vergangenheit liegen, nicht ueber der des Vorgaengers.
        abstand_klasse = "rueckwaerts"
    elif abstand < 120:
        abstand_klasse = "sofort"
    elif abstand < ZIELABSTAND / 2:
        abstand_klasse = "schnell"
    elif abstand <= ZIELABSTAND * 1.5:
        abstand_klasse = "normal"
    else:
        abstand_klasse = "langsam"

    arbeit, arbeit_einheit = _arbeit(kopf.get("difficulty"))

    # ---- Einordnung gegen die Vorgaenger
    eigene = _kennzahlen(s)
    gesammelt = [_kennzahlen(x) for x in reihe]
    urteil = {}
    for name in ("medianrate", "minrate", "fuellung", "txs", "gebuehren"):
        u = _urteil(eigene.get(name), [g.get(name) for g in gesammelt])
        if u:
            urteil[name] = u

    # Die Fensterbreite ist gemessen, nicht gerechnet: Zeitstempel des
    # aeltesten Vergleichsblocks bis zu diesem hier. Bei 10-Minuten-Annahme
    # waeren zwoelf Bloecke immer zwei Stunden - in Wirklichkeit selten.
    zeiten = [x.get("time") for x in reihe if x.get("time")]
    fenster_stunden = None
    if zeiten and zeit:
        fenster_stunden = max(0.0, (zeit - min(zeiten)) / 3600.0)

    return {
        "gefunden": True,
        "grund": None,
        "btc": lambda sat: btc_text(sat, sprache),

        # Kopfdaten - immer da, auch ohne getblockstats
        "hoehe": hoehe,
        "hash": blockhash,
        "zeit": zeit,
        "zeit_text": uhrzeit(zeit),
        "alter": (int(time.time()) - zeit) if zeit else None,
        "bestaetigungen": bestaetigungen if not verwaist else None,
        "endgueltig": bool(bestaetigungen and bestaetigungen >= ENDGUELTIG_AB),
        "verwaist": verwaist,
        "vorgaenger": (hoehe - 1) if (hoehe and not verwaist) else None,
        "nachfolger": (hoehe + 1) if (kopf.get("nextblockhash") and hoehe is not None) else None,
        "spitze": spitze,
        "ist_spitze": hoehe is not None and hoehe == spitze,
        "merkle": kopf.get("merkleroot"),
        "nonce": kopf.get("nonce"),
        "bits": kopf.get("bits"),
        "fassung": kopf.get("versionHex"),
        "schwierigkeit": kopf.get("difficulty"),
        "arbeit": arbeit,
        "arbeit_einheit": arbeit_einheit,
        "nullen": _fuehrende_nullen(blockhash),
        "abstand": abstand,
        "abstand_minuten": (abs(abstand) / 60.0) if abstand is not None else None,
        "abstand_klasse": abstand_klasse,

        # Alles ab hier haengt an getblockstats und darf fehlen.
        "stats_da": bool(stats),
        "txs": txs,
        "zahlungen": zahlungen,
        "leerer_block": txs == 1 if txs else False,
        "gewicht": gewicht,
        "groesse": groesse,
        "fuellung": fuellung,
        "fuellung_klasse": fuellung_klasse,
        "subsidy_sat": subsidy,
        "gebuehren_sat": gebuehren,
        "lohn_sat": lohn,
        "gebuehren_anteil": gebuehrenanteil,
        "anteil_nach_halbierung": anteil_nach_halbierung,
        "epoche": epoche,
        "halbierung": halbierung,
        "bis_halbierung": bis_halbierung,
        "tage_halbierung": tage_halbierung,
        "bewegt_sat": s.get("total_out"),
        "min_rate": minrate,
        "median_rate": medianrate,
        "max_rate": maxrate,
        "avg_rate": s.get("avgfeerate"),
        "min_gebuehr": s.get("minfee"),
        "median_gebuehr": s.get("medianfee"),
        "max_gebuehr": s.get("maxfee"),
        "avg_gebuehr": s.get("avgfee"),
        "perzentile": perzentile,
        "balken": _balken(perzentile),
        "spanne": spanne,
        "spanne_klasse": spanne_klasse,
        "max_vielfaches": max_vielfaches,
        "segwit_txs": segwit_txs,
        "segwit_bytes": segwit_bytes,
        "swtxs": swtxs,
        "ein": ein,
        "aus": aus,
        "utxo": utxo,
        "utxo_klasse": utxo_klasse,
        "utxo_bytes": s.get("utxo_size_inc"),
        "tx_bytes_schnitt": s.get("avgtxsize"),
        "tx_bytes_median": s.get("mediantxsize"),
        "tx_bytes_max": s.get("maxtxsize"),

        # Einordnung
        "urteil": urteil,
        "fenster": len(reihe),
        "fenster_stunden": fenster_stunden,
    }
