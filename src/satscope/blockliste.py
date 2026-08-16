"""Die Blockliste /blocks - fuenfundzwanzig Bloecke, je Block sechs Zahlen.

Die Blockseite erklaert EINEN Block ausfuehrlich. Diese Liste macht das
Gegenteil: viele Bloecke, wenige Zahlen, und die einzige Grafik je Zeile ist
der Fuellstand. Wer hier landet, sucht einen Block - er liest nicht.

KOSTEN, und warum der Zwischenspeicher keine Kuer ist
    getblockhash    <1 ms
    getblockstats   19-30 ms   <-- der ganze Preis
Ein Block kostet also rund 25 ms, fuenfundzwanzig Bloecke 625 ms. Das ist zu
viel fuer jeden Seitenaufruf, und es ist vor allem zu viel fuer einen Knoten,
auf dem echtes Geld liegt. Bestaetigte Bloecke aendern sich aber nicht mehr -
also werden sie einmal geholt und behalten.

⚠️ "AENDERN SICH NICHT MEHR" GILT NICHT AN DER SPITZE. Bei einer Reorganisation
bleibt die HOEHE bestehen, waehrend ein anderer Block sie einnimmt; ein
Speicher, der nach Hoehe schlaegt, wuerde den verwaisten Block weiterzeigen -
unbemerkt, weil alle Zahlen plausibel aussehen. Deshalb wandert ein Block erst
in den Speicher, wenn TIEFE_SICHER Bloecke auf ihm liegen. Die obersten sechs
werden bei jedem Aufruf frisch geholt, das kostet ~150 ms und ist der Preis
dafuer, keine Kette zu zeigen, die es nicht mehr gibt.

ABGELEITETES GEHOERT NICHT IN DEN SPEICHER. Im Speicher liegen ausschliesslich
Tatsachen des Blocks (Zeitstempel, Anzahl, Groesse, Gebuehren). Alles, was sich
mit der Zeit oder mit der Seite aendert - das Alter, die Einfaerbung im
Vergleich zu den Nachbarn -, wird bei JEDER Auslieferung neu gerechnet. Ein
mitgespeichertes "vor drei Minuten" waere zwei Stunden spaeter eine Luege.

KEIN SCHLOSS um die Erhebung, anders als in kette.py: das Band dort wird von
jedem offenen Browserfenster alle fuenf Sekunden gezogen, diese Liste holt
jemand von Hand. Gleichzeitige Aufrufe deckelt bereits die Viererschranke des
Tores; ein Schloss wuerde hier nur den zweiten Leser hinter dem ersten
anstellen, ohne den Knoten weiter zu entlasten.

Jeder Wert ist EINZELN abgesichert (wie knoten._sicher): faellt getblockstats
fuer einen Block aus, bleibt seine Zeile stehen und traegt dort Striche. Auf
einem beschnittenen Knoten ist genau das der Normalfall fuer alte Bloecke.
"""
import asyncio
import time

from .rpc import RpcFehler

# Fuenfundzwanzig Zeilen sind eine Bildschirmseite, ohne endloses Rollen.
SEITE = 25

# Hoechstens so viele Bloecke bleiben im Speicher. Ein Blockeintrag ist rund
# 300 Byte, 500 Eintraege also gut 150 kB - das traegt auch ein Pi, und weiter
# als zwanzig Seiten blaettert ohnehin niemand zurueck.
SPEICHER_MAX = 500

# Ab so vielen aufliegenden Bloecken gilt ein Block als unumkehrbar und darf in
# den Speicher. Dieselbe Schwelle wie in blockseite.ENDGUELTIG_AB - sie ist
# Konvention, keine Konsensregel, aber eine Reorganisation dieser Tiefe hat es
# im Mainnet seit 2013 nicht gegeben.
TIEFE_SICHER = 6

# Konsensgrenze: 4.000.000 Gewichtseinheiten. Die Fuellung wird daran gemessen
# und NICHT an einem Megabyte - seit SegWit waere das die falsche Grenze.
GEWICHTSGRENZE = 4000000

# Ab diesem Alter zeigt die Liste ein Datum statt "vor n Stunden". Bei einem
# Block von 2013 waere "vor 114.000 Stunden" eine Zahl, die niemand liest.
FRISCH_BIS = 86400

# Nur diese Felder holen wir. Das ist keine Kosmetik: bitcoind rechnet nur,
# wonach gefragt wird - jedes weggelassene Feld ist gesparte Knotenzeit.
FELDER = ["time", "total_size", "total_weight", "txs", "feerate_percentiles",
          "medianfee", "subsidy", "totalfee"]

# hoehe -> Blockdaten. Reine Tatsachen, siehe Kopf.
_speicher = {}


async def _sicher(tor, methode, *argumente):
    """Ruft auf und liefert None statt zu werfen - wie knoten._sicher.

    Noch einmal hier und nicht importiert: der Strich statt einer erfundenen
    Zahl ist die wichtigste Regel dieses Projekts und soll in jedem Modul
    sichtbar dastehen, nicht hinter einem Import verschwinden.
    """
    try:
        return await tor.ruf(methode, *argumente)
    except (RpcFehler, OSError, asyncio.TimeoutError):
        return None


def _wert(daten, name):
    """Eine Zahl aus einer RPC-Antwort - oder None.

    Kein Ersatzwert, keine Null: eine fehlende Groesse muss als Strich sichtbar
    werden, sonst steht in der Liste eine Belohnung von 0 BTC, die es nie gab.
    Der bool-Ausschluss ist noetig, weil True in Python eine Zahl ist.
    """
    if not isinstance(daten, dict):
        return None
    w = daten.get(name)
    return w if isinstance(w, (int, float)) and not isinstance(w, bool) else None


def uhrzeit(stempel, kurz=False):
    """Zeitstempel als UTC. Keine Ortszeit: der Server kennt die des Browsers
    nicht, und eine falsche Zeitzone ist schlimmer als eine fremde."""
    if not stempel:
        return None
    form = "%Y-%m-%d %H:%M" if kurz else "%Y-%m-%d %H:%M:%S"
    return time.strftime(form, time.gmtime(int(stempel))) + " UTC"


# --------------------------------------------------------------- Speicher
def _merken(block):
    """Einen fertigen Block behalten - aeltester Zugriff fliegt zuerst raus."""
    _speicher[block["hoehe"]] = block
    while len(_speicher) > SPEICHER_MAX:
        # Ein dict behaelt seine Einfuegereihenfolge; der erste Schluessel ist
        # damit der am laengsten nicht mehr angefasste (siehe _erinnern).
        del _speicher[next(iter(_speicher))]


def _erinnern(hoehe):
    """Aus dem Speicher holen und ans Ende ruecken (jung halten)."""
    block = _speicher.pop(hoehe, None)
    if block is not None:
        _speicher[hoehe] = block
    return block


def leeren():
    """Speicher vergessen - fuer Tests. Im Betrieb ruft das niemand."""
    _speicher.clear()


# --------------------------------------------------------------- Erhebung
def _leerer_block(hoehe):
    """Eine Zeile, die nur ihre Hoehe kennt. Alles andere wird zum Strich."""
    return {"hoehe": hoehe, "hash": None, "zeit": None, "zeit_text": None,
            "zeit_kurz": None, "txs": None, "groesse": None, "gewicht": None,
            "fuellung": None, "median_rate": None, "median_gebuehr_sat": None,
            "subsidy_sat": None, "gebuehren_sat": None, "lohn_sat": None,
            "stats_da": False}


async def _ein_block(tor, hoehe):
    """Ein Block als schlichtes dict, jeder Wert einzeln abgesichert.

    Faellt getblockhash aus, bleibt die Hoehe stehen und die Zeile zeigt
    Striche. Faellt nur getblockstats aus, ist wenigstens der Hash da.
    """
    block = _leerer_block(hoehe)

    hasch = await _sicher(tor, "getblockhash", hoehe)
    if not isinstance(hasch, str):
        return block
    block["hash"] = hasch

    # Ueber den HASH und nicht ueber die Hoehe: faellt zwischen beiden Aufrufen
    # eine Reorganisation, antwortet getblockstats auf den verwaisten Hash gar
    # nicht - besser ein Strich als Zahlen aus einer anderen Kette unter einer
    # Hoehe, die inzwischen jemand anderem gehoert.
    st = await _sicher(tor, "getblockstats", hasch, FELDER)
    if not isinstance(st, dict):
        return block

    block["stats_da"] = True
    block["zeit"] = _wert(st, "time")
    block["zeit_text"] = uhrzeit(block["zeit"])
    block["zeit_kurz"] = uhrzeit(block["zeit"], kurz=True)
    block["txs"] = _wert(st, "txs")
    block["groesse"] = _wert(st, "total_size")
    block["gewicht"] = _wert(st, "total_weight")
    if block["gewicht"] is not None:
        block["fuellung"] = block["gewicht"] / GEWICHTSGRENZE * 100.0

    # ⚠️ medianfee ist die mittlere ABSOLUTE Gebuehr einer Transaktion in
    # Satoshi - NICHT ihr Preis je Byte. Vergleichbar zwischen Bloecken ist nur
    # der Preis je Byte, und der steht im 50. Perzentil der Gewichtseinheiten.
    # Deshalb traegt die Spalte die Rate; die absolute Gebuehr faehrt als
    # zweiter Wert mit, ohne die Liste zu fuellen.
    block["median_gebuehr_sat"] = _wert(st, "medianfee")
    p = st.get("feerate_percentiles")
    if isinstance(p, list) and len(p) == 5 and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) for x in p):
        block["median_rate"] = p[2]

    # Was der Miner verdient hat: frische Muenzen plus Gebuehren. Nur wenn
    # BEIDE Zahlen da sind - eine Summe aus einer Zahl und einer Luecke waere
    # falsch, nicht unvollstaendig.
    block["subsidy_sat"] = _wert(st, "subsidy")
    block["gebuehren_sat"] = _wert(st, "totalfee")
    if block["subsidy_sat"] is not None and block["gebuehren_sat"] is not None:
        block["lohn_sat"] = block["subsidy_sat"] + block["gebuehren_sat"]
    return block


def _vollstaendig(block):
    """Darf dieser Block in den Speicher? Nur ganz oder gar nicht.

    Ein Block mit ausgefallenem getblockstats wuerde sich sonst mit seinen
    Luecken einbrennen und beim naechsten Aufruf nicht mehr nachgeholt.
    """
    return bool(block.get("hash")) and block.get("stats_da")


def _hitze(zeilen):
    """Wo liegt die Gebuehrenrate jeder Zeile zwischen der billigsten und der
    teuersten DIESER Seite? 0.0 bis 1.0, sonst None.

    Das ist der Bezugsrahmen, den eine nackte Zahl nicht hat: "2,4 sat/vB" sagt
    nichts, "der teuerste der Seite" schon. Bewusst nur relativ zum sichtbaren
    Ausschnitt - eine Skala ueber die ganze Kette waere eine Behauptung, die
    wir nicht erheben.

    Unter drei Messwerten bleibt es aus: bei zwei Bloecken ist "der teuerste"
    keine Beobachtung. Sind alle gleich, ebenfalls - dann gaebe es nur eine
    Faerbung, die einen Unterschied vortaeuscht.
    """
    raten = [z["median_rate"] for z in zeilen if z["median_rate"] is not None]
    if len(raten) < 3:
        return
    tief, hoch = min(raten), max(raten)
    if hoch <= tief:
        return
    for z in zeilen:
        if z["median_rate"] is not None:
            z["hitze"] = round((z["median_rate"] - tief) / (hoch - tief), 3)


def vor_aus_parameter(text):
    """?vor=<hoehe> einlesen: int oder None. Nimmt nur reine ASCII-Ziffern.

    Alles andere - Buchstaben, Minus, arabisch-indische Ziffern, eine
    tausendstellige Zahl - wird zu None und damit zur neuesten Seite. Eine
    Fehlermeldung waere hier Ballast: die Adresszeile ist kein Formular.
    """
    t = (text or "").strip()
    if t and t.isascii() and t.isdigit() and len(t) <= 9:
        return int(t)
    return None


async def liste(tor, vor=None):
    """Die Seite: bis zu SEITE Bloecke, neuester zuerst.

    `vor=None` ist die Spitze der Kette; `vor=<hoehe>` zeigt die Bloecke
    UNTERHALB dieser Hoehe - ausschliesslich, so wie das Wort es sagt.
    """
    jetzt = int(time.time())

    # Erst die Spitze. Der Aufruf kostet 9 ms und ist die Bremse fuer den Fall,
    # dass der Knoten gar nicht da ist: ohne ihn liefen 50 Aufrufe in je fuenf
    # Sekunden Zeitlimit - bei Viererschranke ueber eine Minute Wartezeit fuer
    # eine Seite, die ohnehin leer bleibt.
    info = await _sicher(tor, "getblockchaininfo")
    spitze = _wert(info, "blocks")
    if spitze is None:
        return {"erreichbar": False, "spitze": None, "oben": None, "unten": None,
                "bloecke": [], "seite": SEITE, "neuer": None, "aelter": None,
                "zur_spitze": None, "jetzt": jetzt}
    spitze = int(spitze)

    oben = spitze if vor is None else min(int(vor) - 1, spitze)
    unten = max(0, oben - SEITE + 1)
    hoehen = list(range(oben, unten - 1, -1)) if oben >= 0 else []

    # Was schon dasteht, wird nicht neu geholt. Was im Speicher liegt, liegt
    # dort nur, weil es tief genug war - tiefer wird es mit der Zeit von selbst.
    aus_speicher = {h: _erinnern(h) for h in hoehen}
    fehlend = [h for h in hoehen if aus_speicher[h] is None]

    if fehlend:
        # Nebenlaeufig, aber nicht ungebremst: das Tor laesst nur vier Aufrufe
        # gleichzeitig durch, damit bitcoinds rpcworkqueue frei bleibt.
        for block in await asyncio.gather(*[_ein_block(tor, h) for h in fehlend]):
            aus_speicher[block["hoehe"]] = block
            if _vollstaendig(block) and spitze - block["hoehe"] >= TIEFE_SICHER:
                _merken(block)

    # Kopien: was hier drankommt (Alter, Hitze, Spitzenmarke), gilt fuer diesen
    # Aufruf und darf den Speicher nicht anfassen.
    zeilen = []
    for h in hoehen:
        z = dict(aus_speicher[h] or _leerer_block(h))
        z["alter"] = (jetzt - z["zeit"]) if z["zeit"] else None
        z["frisch"] = z["alter"] is not None and z["alter"] < FRISCH_BIS
        z["ist_spitze"] = h == spitze
        z["hitze"] = None
        zeilen.append(z)
    _hitze(zeilen)

    # Blaettern. `vor` ist ausschliesslich: die naechste Seite beginnt unter dem
    # untersten Block dieser Seite. Nach oben wird auf /blocks zurueckgefallen,
    # sobald die neuere Seite ohnehin an der Spitze haengt - sonst zeigte ein
    # gemerkter Link auf eine Hoehe, die morgen mitten in der Kette liegt.
    hoch = oben + SEITE + 1
    return {
        "erreichbar": True,
        "spitze": spitze,
        "oben": oben if hoehen else None,
        "unten": unten if hoehen else None,
        "bloecke": zeilen,
        "seite": SEITE,
        "neuer": (None if oben >= spitze
                  else ("/blocks" if hoch > spitze else "/blocks?vor=%d" % hoch)),
        "aelter": ("/blocks?vor=%d" % unten) if hoehen and unten > 0 else None,
        "zur_spitze": None if oben >= spitze else "/blocks",
        "jetzt": jetzt,
    }
