"""Spielerische Kennzahlen - der Teil, den man gern ansieht.

Alles kommt aus BILLIGEN Aufrufen (siehe BILLIG in rpc.py); die teuren kennt
das Web-Tor gar nicht. Gemessene Kosten der hier benutzten Methoden am Knoten:
getmininginfo 7 ms · getnettotals 7 ms · estimatesmartfee 7 ms ·
estimaterawfee 15 ms · getblockstats 19-30 ms · getblockhash <1 ms ·
getblockheader <5 ms. Eine volle Erhebung liegt damit bei rund 40 Aufrufen,
die das Tor auf vier gleichzeitige deckelt - Groessenordnung 0,3 s Wandzeit
und unter 1 s Knotenzeit. Nichts davon beruehrt die UTXO-Menge.

Drei Regeln bestimmen den Aufbau:

* Jeder Wert ist EINZELN abgesichert. Faellt ein Aufruf aus, ist genau dieser
  Wert None und die Vorlage zeigt dort einen Strich. Nie wird etwas geraten,
  nie faellt wegen einer fehlenden Zahl eine ganze Karte weg.

* Geschaetzt wird nur, wo die Zukunft gemeint ist (Zeitpunkt der Halbierung,
  der Schwierigkeitsanpassung, Transaktionen seit dem letzten Block). Diese
  Werte tragen in der Vorlage ein "≈" - eine Schaetzung, die wie eine Messung
  aussieht, ist eine Luege.

* Dieses Modul kennt KEINE Texte. Es liefert Rohwerte und Schluessel; die
  Woerter stehen im Katalog. Einzige Ausnahme sind die Ueberraschungszeilen:
  die werden fertig gesetzt, weil das JavaScript sie durchblaettert und im
  Browser keinen Katalog hat.
"""
import asyncio
import math
import random
import time

from .rpc import RpcFehler

HALBIERUNG_ALLE = 210000
PERIODE = 2016
ZIEL_TAKT = 600                  # Sekunden je Block, so ist Bitcoin geeicht
SAT_JE_BTC = 100000000

# Eine gewoehnliche Zahlung: ein Eingang, zwei Ausgaenge, P2WPKH = 141 vB.
# Erst damit wird aus "sat/vB" ein Betrag, den man sich vorstellen kann.
TYPISCHE_VB = 141

# Bestaetigungsziele fuer estimatesmartfee: naechster Block, halbe Stunde,
# eine Stunde, ein Tag.
ZIELE = (1, 3, 6, 144)

# So viele Bloecke werden als Probe gezogen. Sechs, weil das rund eine Stunde
# abdeckt: kurz genug, um "gerade jetzt" zu heissen, lang genug, damit ein
# einzelner Ausreisser den Takt nicht umwirft. Kosten: 6 x 19-30 ms.
PROBE_BLOECKE = 6

# Nur diese Felder holen - getblockstats rechnet dann auch nur diese aus.
STATS = ("height", "time", "txs", "total_size", "total_weight", "totalfee",
         "subsidy", "avgfeerate", "minfeerate", "maxfeerate", "swtxs",
         "utxo_increase", "ins", "outs", "feerate_percentiles")

# Obergrenze fuer die Rueckwaertssuche nach Mitternacht. 1200 Bloecke waeren
# ein Tag mit 72-Sekunden-Takt - das gibt es nicht, aber die Schleife soll
# auch bei kaputten Zeitstempeln enden statt zu laufen.
MAX_ZURUECK = 1200


async def _sicher(tor, methode, *argumente):
    """Ruft auf und liefert None statt zu werfen.

    Bewusst dieselbe Absicherung wie _sicher() in knoten.py und bewusst hier
    noch einmal: ein Umbau dort soll diese Seite nicht mitreissen.
    """
    try:
        return await tor.ruf(methode, *argumente)
    except (RpcFehler, OSError, asyncio.TimeoutError):
        return None


# ------------------------------------------------------------ kleine Helfer
def _zahl(wert):
    """Nur echte Zahlen durchlassen. bool ist in Python eine Zahl - hier nicht."""
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        return None
    if isinstance(wert, float) and (math.isnan(wert) or math.isinf(wert)):
        return None
    return wert


def _sat_pro_vb(btc_je_kvb):
    """bitcoind rechnet Gebuehren in BTC je kvB. Menschen in sat/vB."""
    wert = _zahl(btc_je_kvb)
    if wert is None or wert <= 0:
        return None
    return wert * SAT_JE_BTC / 1000.0


def subvention_sat(hoehe):
    """Blockbelohnung auf dieser Hoehe, in Satoshi.

    50 BTC, alle 210.000 Bloecke halbiert. Ganzzahlige Schiebung statt
    Fliesskomma: bei Geldbetraegen faellt ein Rundungsfehler irgendwann auf.
    Ab der 33. Epoche ist die Belohnung 0 - die Schiebung liefert das von
    allein, sobald 5.000.000.000 leergeschoben ist.
    """
    if hoehe is None or hoehe < 0:
        return None
    epoche = hoehe // HALBIERUNG_ALLE
    if epoche >= 64:
        return 0
    return 5000000000 >> epoche


async def _kopf(tor, hoehe):
    """Blockkopf zu einer Hoehe. Zwei der billigsten Aufrufe ueberhaupt."""
    if hoehe is None or hoehe < 0:
        return None
    h = await _sicher(tor, "getblockhash", hoehe)
    if not h:
        return None
    return await _sicher(tor, "getblockheader", h)


async def _blockstats(tor, hoehe):
    """getblockstats mit Feldfilter - und Rueckfall ohne Filter.

    Der Filter macht den Aufruf billiger, haengt aber an Feldnamen. Wuerde ein
    aelterer Knoten einen davon nicht kennen, faellt der ganze Aufruf aus und
    mit ihm ein halbes Dutzend Kennzahlen. Deshalb einmal ungefiltert
    nachfassen: kostet nur im Fehlerfall, und dort ist es die Rettung.
    """
    b = await _sicher(tor, "getblockstats", hoehe, list(STATS))
    if b:
        return b
    return await _sicher(tor, "getblockstats", hoehe)


# ------------------------------------------------------------ Erhebungen
async def _probe(tor, hoehe, anzahl=PROBE_BLOECKE):
    """Die letzten Bloecke als Statistikprobe, aelteste zuerst."""
    if hoehe is None:
        return []
    hoehen = [h for h in range(hoehe - anzahl + 1, hoehe + 1) if h >= 0]
    roh = await asyncio.gather(*[_blockstats(tor, h) for h in hoehen])
    return [b for b in roh if isinstance(b, dict) and b.get("time")]


def _takt_und_rate(probe):
    """(Sekunden je Block, Transaktionen je Sekunde) aus der Probe.

    Zeitstempel sind im Konsens nicht streng monoton - ein Miner darf leicht
    daneben liegen. Eine Spanne <= 0 ist damit moeglich und heisst hier
    schlicht: keine Aussage.
    """
    if len(probe) < 2:
        return None, None
    spanne = (_zahl(probe[-1].get("time")) or 0) - (_zahl(probe[0].get("time")) or 0)
    if spanne <= 0:
        return None, None
    abstaende = len(probe) - 1
    # Der erste Block der Probe liefert nur den Startzeitpunkt; seine
    # Transaktionen liegen VOR der Spanne und duerfen nicht mitzaehlen.
    txe = sum((_zahl(b.get("txs")) or 0) for b in probe[1:])
    return spanne / abstaende, (txe / spanne if txe else None)


async def _heute(tor, hoehe, block_zeit):
    """Bloecke und neue Bitcoin seit Mitternacht (Ortszeit des Knotens).

    Die Grenze wird binaer gesucht: rund sieben Runden zu je zwei sehr
    billigen Aufrufen. Sich von der Spitze Block fuer Block zurueckzuhangeln
    waeren rund 144 Runden - zwanzigmal so teuer fuer dasselbe Ergebnis.

    ⚠️ Blockzeitstempel sind nicht streng monoton, die Grenze kann also um
    ein, zwei Bloecke danebenliegen. Fuer einen Tageszaehler ist das ohne
    Belang - fuer eine Abrechnung waere es das nicht.
    """
    leer = {"bloecke": None, "subvention_sat": None, "seit": None}
    if hoehe is None or block_zeit is None:
        return leer

    o = time.localtime(time.time())
    # tm_isdst=-1: die Bibliothek soll die Sommerzeit selbst entscheiden.
    mitternacht = int(time.mktime(
        (o.tm_year, o.tm_mon, o.tm_mday, 0, 0, 0, 0, 0, -1)))
    if block_zeit < mitternacht:
        # Der letzte Block ist aelter als Mitternacht - heute kam noch keiner.
        return {"bloecke": 0, "subvention_sat": 0, "seit": mitternacht}

    # Klammer: rueckwaerts verdoppeln, bis ein Block VOR Mitternacht liegt.
    schritt = 160
    unten = max(0, hoehe - schritt)
    erster = None
    while True:
        kopf = await _kopf(tor, unten)
        if kopf is None:
            return leer
        if (_zahl(kopf.get("time")) or 0) < mitternacht:
            break
        if unten == 0:
            # Die ganze Kette liegt hinter Mitternacht - moeglich nur auf
            # einem frisch erzeugten Testnetz, aber dann stimmt es auch.
            erster = 0
            break
        schritt *= 2
        if schritt > MAX_ZURUECK:
            return leer
        unten = max(0, hoehe - schritt)

    if erster is None:
        # Binaere Suche auf den ersten Block, der nicht mehr vor Mitternacht
        # liegt. Unten liegt davor, oben (die Spitze) dahinter - geprueft.
        oben = hoehe
        while unten + 1 < oben:
            mitte = (unten + oben) // 2
            kopf = await _kopf(tor, mitte)
            if kopf is None:
                return leer
            if (_zahl(kopf.get("time")) or 0) < mitternacht:
                unten = mitte
            else:
                oben = mitte
        erster = oben

    anzahl = hoehe - erster + 1
    # Ueber eine Halbierung hinweg ist die Belohnung nicht konstant, deshalb
    # je Block bestimmt. Das ist reine Arithmetik, kein weiterer Aufruf.
    summe = sum(subvention_sat(h) or 0 for h in range(erster, hoehe + 1))
    return {"bloecke": anzahl, "subvention_sat": summe, "seit": mitternacht}


async def _gebuehren(tor):
    """estimatesmartfee fuer alle Ziele, jedes einzeln abgesichert."""
    roh = await asyncio.gather(*[_sicher(tor, "estimatesmartfee", n)
                                 for n in ZIELE])
    raus = []
    for ziel, e in zip(ZIELE, roh):
        # Bei zu duenner Datenlage antwortet bitcoind mit "errors" statt
        # "feerate". Dann fehlt genau dieser Balken, nicht die ganze Karte.
        rate = _sat_pro_vb((e or {}).get("feerate"))
        raus.append({
            "ziel": ziel,
            "sat_vb": rate,
            "kosten_sat": int(round(rate * TYPISCHE_VB)) if rate else None,
        })
    return raus


def _rohgebuehr(roh):
    """estimaterawfee: die ungeglaettete Sicht des Schaetzers.

    estimatesmartfee glaettet und rundet auf; estimaterawfee zeigt, was der
    kurze Horizont wirklich gemessen hat. Die Differenz ist die interessante
    Zahl - sie sagt, wie sehr die Empfehlung Sicherheitsabstand haelt.
    """
    kurz = (roh or {}).get("short")
    if not isinstance(kurz, dict):
        return None
    return _sat_pro_vb(kurz.get("feerate"))


# ------------------------------------------------------------ Hauptzugang
async def erhebe(tor, z=None):
    """Alle Rohwerte fuer die Spielkarten. Keine Texte, keine Sprache.

    `z` ist der bereits erhobene Zustand aus knoten.zustand(). Wird er
    durchgereicht, sparen wir getblockchaininfo und getmempoolinfo ein
    zweites Mal - dieselbe Seite soll den Knoten nicht doppelt fragen.
    """
    if z is None:
        kette, mempool = await asyncio.gather(
            _sicher(tor, "getblockchaininfo"), _sicher(tor, "getmempoolinfo"))
        z = {
            "hoehe": (kette or {}).get("blocks"),
            "platte_bytes": (kette or {}).get("size_on_disk"),
            "mempool_bytes": (mempool or {}).get("bytes"),
            "mempool_anzahl": (mempool or {}).get("size"),
            "mempool_min_gebuehr": (mempool or {}).get("mempoolminfee"),
            "block_zeit": None,
        }

    hoehe = _zahl(z.get("hoehe"))
    if hoehe is not None:
        hoehe = int(hoehe)
    block_zeit = _zahl(z.get("block_zeit"))
    if block_zeit is None and hoehe is not None:
        # Vor dem grossen Rutsch, nicht in ihm: die Suche nach Mitternacht
        # braucht die Zeit der Spitze als Ausgangspunkt.
        block_zeit = _zahl((await _kopf(tor, hoehe) or {}).get("time"))

    periode_start = (hoehe // PERIODE) * PERIODE if hoehe is not None else None
    letzte_halbierung = ((hoehe // HALBIERUNG_ALLE) * HALBIERUNG_ALLE
                         if hoehe is not None else None)

    (mining, netz, gebuehren, roh, probe, kopf_periode, kopf_halbierung,
     heute) = await asyncio.gather(
        _sicher(tor, "getmininginfo"),
        _sicher(tor, "getnettotals"),
        _gebuehren(tor),
        _sicher(tor, "estimaterawfee", 1),
        _probe(tor, hoehe),
        _kopf(tor, periode_start),
        _kopf(tor, letzte_halbierung),
        _heute(tor, hoehe, block_zeit),
    )

    takt, tx_rate = _takt_und_rate(probe)
    letzter = probe[-1] if probe else {}

    d = {
        "hoehe": hoehe,
        "block_zeit": block_zeit,
        "takt_s": takt,
        "tx_rate": tx_rate,
        "probe": probe,
        "letzter": letzter,
        "hashrate": _zahl((mining or {}).get("networkhashps")),
        "schwierigkeit": _zahl((mining or {}).get("difficulty")),
        "platte_bytes": _zahl(z.get("platte_bytes")),
        "netz_rein": _zahl((netz or {}).get("totalbytesrecv")),
        "netz_raus": _zahl((netz or {}).get("totalbytessent")),
        "heute": heute,
        "gebuehren": gebuehren,
        "roh_sat_vb": _rohgebuehr(roh),
        "mempool_min_sat_vb": _sat_pro_vb(z.get("mempool_min_gebuehr")),
    }

    d["halbierung"] = _halbierung(hoehe)
    d["anpassung"] = _anpassung(hoehe, block_zeit, kopf_periode, takt,
                                d["schwierigkeit"])
    d["mempool"] = _mempool(z, d["gebuehren"])
    d["block"] = _block(letzter)
    d["schwierigkeit_start"] = _zahl((kopf_halbierung or {}).get("difficulty"))
    return d


def _halbierung(hoehe):
    """Zaehler bis zur naechsten Halbierung."""
    if hoehe is None:
        return {"epoche": None, "verbleibend": None, "fortschritt_p": None,
                "sekunden": None, "zeitpunkt": None, "hoehe": None,
                "subvention_sat": None, "naechste_sat": None}
    epoche = hoehe // HALBIERUNG_ALLE
    ziel = (epoche + 1) * HALBIERUNG_ALLE
    verbleibend = ziel - hoehe
    # ⚠️ Hier bewusst mit 600 s gerechnet, NICHT mit dem gemessenen Takt der
    # letzten Stunde: bis zur Halbierung sind es Jahre, und die
    # Schwierigkeitsanpassung zieht den Takt ueber diese Zeit zurueck auf
    # zehn Minuten. Der Tagestakt hochgerechnet auf vier Jahre waere Unsinn.
    sekunden = verbleibend * ZIEL_TAKT
    return {
        "epoche": epoche,
        "hoehe": ziel,
        "verbleibend": verbleibend,
        "fortschritt_p": (hoehe % HALBIERUNG_ALLE) * 100.0 / HALBIERUNG_ALLE,
        "sekunden": sekunden,
        "zeitpunkt": int(time.time() + sekunden),
        "subvention_sat": subvention_sat(hoehe),
        "naechste_sat": subvention_sat(ziel),
    }


def _anpassung(hoehe, block_zeit, kopf_periode, takt, schwierigkeit):
    """Schaetzung der naechsten Schwierigkeitsanpassung.

    Rechnung: die Schwierigkeit wird alle 2016 Bloecke so nachgestellt, dass
    2016 Bloecke wieder zwei Wochen dauern. Aus der bisher verstrichenen Zeit
    der laufenden Periode laesst sich die Aenderung hochrechnen.

    Zwei Feinheiten, die man kennen muss:

    * Der Konsens misst von Block `periode_start` bis `periode_start + 2015`,
      also 2015 Abstaende, vergleicht sie aber mit 2016 * 600 s (die bekannte
      Abweichung um eins). Der Fehler liegt bei 0,05 % - kleiner als jede
      Schwankung, die man hier sieht. Wir rechnen die einfache Variante, die
      auch die bekannten Explorer zeigen, damit die Zahlen vergleichbar sind.
    * Der Konsens deckelt die Aenderung auf Faktor 4 nach oben und 1/4 nach
      unten. Ohne diesen Deckel koennte die Anzeige nach einem Ausreisser
      voellig unmoegliche Prozente behaupten.
    """
    leer = {"verbleibend": None, "fortschritt_p": None, "aenderung_p": None,
            "takt_s": None, "sekunden": None, "zeitpunkt": None,
            "im_zeitraum": None, "neu": None}
    if hoehe is None:
        return leer

    im_zeitraum = hoehe % PERIODE
    # So zaehlen auch die gaengigen Explorer: der letzte dieser Bloecke ist
    # bereits der erste mit der neuen Schwierigkeit.
    verbleibend = PERIODE - im_zeitraum
    d = dict(leer)
    d["verbleibend"] = verbleibend
    d["im_zeitraum"] = im_zeitraum
    d["fortschritt_p"] = im_zeitraum * 100.0 / PERIODE

    start_zeit = _zahl((kopf_periode or {}).get("time"))
    if im_zeitraum > 0 and start_zeit is not None and block_zeit is not None:
        ist = block_zeit - start_zeit
        if ist > 0:
            periodentakt = ist / im_zeitraum
            faktor = min(4.0, max(0.25, (im_zeitraum * ZIEL_TAKT) / ist))
            d["aenderung_p"] = (faktor - 1.0) * 100.0
            d["takt_s"] = periodentakt
            if schwierigkeit:
                d["neu"] = schwierigkeit * faktor

    # Fuer den ZEITPUNKT zaehlt der gemessene Takt, nicht die Eichung: die
    # Anpassung ist Tage entfernt, so lange haelt das aktuelle Tempo meist an.
    tempo = takt or d["takt_s"] or ZIEL_TAKT
    d["sekunden"] = verbleibend * tempo
    d["zeitpunkt"] = int(time.time() + d["sekunden"])
    return d


def _mempool(z, gebuehren):
    """Der Mempool in Bloecken statt in Megabyte - das versteht man sofort."""
    b = _zahl(z.get("mempool_bytes"))
    # getmempoolinfo.bytes zaehlt virtuelle Groesse; ein Block fasst rund
    # 1.000.000 vB. Die Division ist damit "wie viele Bloecke tief".
    bloecke = (b / 1000000.0) if b is not None else None
    return {
        "bytes": b,
        "anzahl": _zahl(z.get("mempool_anzahl")),
        "bloecke": bloecke,
        # Wie lange der Rueckstau bei Zieltakt braucht, um abgearbeitet zu
        # sein - unter der Annahme, dass nichts Neues dazukommt. Genau das
        # steht auch als Vorbehalt im Text.
        "leerlauf_s": bloecke * ZIEL_TAKT if bloecke is not None else None,
    }


def _block(letzter):
    """Was im letzten Block passiert ist."""
    if not letzter:
        return {}
    gewicht = _zahl(letzter.get("total_weight"))
    txs = _zahl(letzter.get("txs"))
    sw = _zahl(letzter.get("swtxs"))
    gebuehr = _zahl(letzter.get("totalfee"))
    subv = _zahl(letzter.get("subsidy"))
    perzentile = letzter.get("feerate_percentiles")
    if not (isinstance(perzentile, list) and len(perzentile) == 5):
        perzentile = None
    return {
        "hoehe": _zahl(letzter.get("height")),
        "txs": txs,
        "ins": _zahl(letzter.get("ins")),
        "outs": _zahl(letzter.get("outs")),
        "gebuehr_sat": gebuehr,
        "subvention_sat": subv,
        # Ein Block fasst 4.000.000 Gewichtseinheiten - das ist die harte
        # Konsensgrenze, nicht eine Faustregel.
        "fuellung_p": (gewicht * 100.0 / 4000000.0) if gewicht else None,
        "segwit_p": (sw * 100.0 / txs) if (txs and sw is not None) else None,
        "gebuehrenanteil_p": (gebuehr * 100.0 / (gebuehr + subv))
                             if (gebuehr is not None and subv) else None,
        "min_sat_vb": _zahl(letzter.get("minfeerate")),
        "max_sat_vb": _zahl(letzter.get("maxfeerate")),
        "median_sat_vb": _zahl(perzentile[2]) if perzentile else None,
        "perzentile": perzentile,
        "utxo_zuwachs": _zahl(letzter.get("utxo_increase")),
    }


# ------------------------------------------------------------ Einordnung
def _stufe(wert, grenzen, schluessel):
    """Ordnet einen Wert in Stufen ein und liefert den Textschluessel."""
    if wert is None:
        return None
    for grenze, s in zip(grenzen, schluessel):
        if wert < grenze:
            return s
    return schluessel[-1]


def einordnen(d):
    """Ergaenzt die Rohwerte um Textschluessel - die Einordnung.

    Nicht die nackte Zahl zaehlt, sondern ob sie ungewoehnlich ist. Die
    Schwellen sind bewusst grob: eine Skala mit zwoelf Stufen sagt weniger
    als eine mit fuenf.
    """
    m = d.get("mempool") or {}
    d["andrang_schluessel"] = _stufe(
        m.get("bloecke"), (1, 3, 10, 30),
        ("spiel.congestion.empty", "spiel.congestion.calm",
         "spiel.congestion.normal", "spiel.congestion.busy",
         "spiel.congestion.jammed"))

    # Tempo der letzten Bloecke. Gruen heisst hier "kuerzere Wartezeit fuer
    # den Nutzer", nicht "besser fuers Netz" - Bitcoin ist auf 600 s geeicht
    # und holt Abweichungen von allein zurueck.
    d["tempo_schluessel"] = _stufe(
        d.get("takt_s"), (480, 720),
        ("spiel.pace.fast", "spiel.pace.steady", "spiel.pace.slow"))
    d["tempo_klasse"] = _stufe(
        d.get("takt_s"), (480, 720), ("gut", "", "schlecht"))

    # Gebuehrenlage am Ziel "naechster Block".
    schnell = _gebuehr_fuer(d, 1)
    d["gebuehr_schluessel"] = _stufe(
        schnell, (2, 5, 20, 100),
        ("spiel.fee.dirtcheap", "spiel.fee.cheap", "spiel.fee.normal",
         "spiel.fee.pricey", "spiel.fee.brutal"))
    d["gebuehr_zeiger_p"] = _zeiger(schnell)

    # Wer warten kann, spart - aber nur, wenn es wirklich etwas ausmacht.
    geduld = _gebuehr_fuer(d, 144)
    d["sparen_p"] = None
    if schnell and geduld and geduld < schnell:
        ersparnis = (1 - geduld / schnell) * 100.0
        if ersparnis >= 5:
            d["sparen_p"] = ersparnis

    # Der Aufschlag der geglaetteten Empfehlung gegenueber der Rohmessung.
    d["aufschlag_p"] = None
    if schnell and d.get("roh_sat_vb"):
        auf = (schnell / d["roh_sat_vb"] - 1) * 100.0
        if abs(auf) >= 5:
            d["aufschlag_p"] = auf

    # ⚠️ Die Richtung der Schwierigkeitsanpassung wird bewusst NICHT gruen
    # oder rot gefaerbt. Steigende Schwierigkeit heisst "es wurde schneller
    # gemined", fallende "langsamer" - beides ist weder gut noch schlecht,
    # sondern genau der Regelkreis, der Bitcoin bei zehn Minuten haelt.
    a = d.get("anpassung") or {}
    aenderung = a.get("aenderung_p")
    d["anpassung_richtung"] = (None if aenderung is None
                               else "hoch" if aenderung > 0
                               else "runter" if aenderung < 0 else "gleich")
    return d


def _gebuehr_fuer(d, ziel):
    for g in d.get("gebuehren") or []:
        if g.get("ziel") == ziel:
            return g.get("sat_vb")
    return None


def _zeiger(sat_vb, unten=1.0, oben=1000.0):
    """Gebuehr auf 0-100 fuer das Zifferblatt, logarithmisch.

    Linear waere die Skala unbrauchbar: zwischen 1 und 5 sat/vB liegen Welten
    fuer den Nutzer, zwischen 500 und 504 liegt nichts. Der Logarithmus gibt
    beiden Bereichen denselben Platz.
    """
    if not sat_vb or sat_vb <= 0:
        return None
    wert = math.log10(max(unten, min(oben, sat_vb)) / unten)
    return max(0.0, min(100.0, wert * 100.0 / math.log10(oben / unten)))


# ------------------------------------------------------------ Ueberraschungen
def ueberraschungen(d, t):
    """Wahre Kennzahlen als fertige Saetze - mindestens acht, gemischt.

    ⚠️ Das einzige Stueck dieses Moduls, das Texte setzt: das JavaScript
    blaettert die Zeilen im Browser durch und hat dort keinen Katalog. Die
    Reihenfolge wird gemischt, damit bei jedem Laden eine andere oben steht.

    Jede Zeile prueft ihre eigenen Zutaten. Fehlt eine Zahl, faellt genau
    diese Zeile weg - geraten wird nichts.
    """
    z = []

    def sag(schluessel, **werte):
        z.append(t.t(schluessel, **werte))

    m = d.get("mempool") or {}
    b = d.get("block") or {}
    h = d.get("halbierung") or {}
    a = d.get("anpassung") or {}
    heute = d.get("heute") or {}

    if m.get("bloecke") is not None:
        sag("spiel.fact.mempool_blocks", n=t.zahl(m["bloecke"], 1))
    if m.get("leerlauf_s") and m["leerlauf_s"] >= 1800:
        sag("spiel.fact.drain", n=t.zahl(m["leerlauf_s"] / 3600.0, 1))
    if heute.get("bloecke") is not None:
        sag("spiel.fact.blocks_today" if heute["bloecke"] != 1
            else "spiel.fact.blocks_today_one", n=t.zahl(heute["bloecke"]))
    if heute.get("subvention_sat"):
        sag("spiel.fact.new_btc",
            n=t.zahl(heute["subvention_sat"] / SAT_JE_BTC, 3))
    if d.get("takt_s"):
        sag("spiel.fact.pace", n=t.zahl(len(d.get("probe") or [])),
            min=t.zahl(d["takt_s"] / 60.0, 1))
    if d.get("hashrate"):
        sag("spiel.fact.hashrate", n=t.zahl(d["hashrate"] / 1e18, 0))
    if d.get("schwierigkeit"):
        # Erwartete Versuche je Block: Schwierigkeit mal 2^32. Das ist die
        # Definition der Schwierigkeit, keine Faustregel.
        sag("spiel.fact.hashes",
            n=t.zahl(d["schwierigkeit"] * 4294967296 / 1e21, 0))
    if b.get("gebuehrenanteil_p") is not None:
        sag("spiel.fact.fee_share", p=t.zahl(b["gebuehrenanteil_p"], 1))
    if b.get("min_sat_vb") is not None:
        sag("spiel.fact.cheapest", n=t.zahl(b["min_sat_vb"], 2))
    if b.get("max_sat_vb"):
        sag("spiel.fact.priciest", n=t.zahl(b["max_sat_vb"], 0))
    if b.get("utxo_zuwachs") is not None and b["utxo_zuwachs"] != 0:
        sag("spiel.fact.utxo_grew" if b["utxo_zuwachs"] > 0
            else "spiel.fact.utxo_shrank", n=t.zahl(abs(b["utxo_zuwachs"])))
    if b.get("segwit_p") is not None:
        sag("spiel.fact.segwit", p=t.zahl(b["segwit_p"], 0))
    if b.get("fuellung_p") is not None:
        sag("spiel.fact.fullness", p=t.zahl(b["fuellung_p"], 1))
    if b.get("txs") and b.get("ins") is not None and b.get("outs") is not None:
        sag("spiel.fact.ins_outs", i=t.zahl(b["ins"]), o=t.zahl(b["outs"]))
    if d.get("tx_rate"):
        sag("spiel.fact.tps", n=t.zahl(d["tx_rate"], 1))
    if d.get("platte_bytes"):
        sag("spiel.fact.disk", n=t.zahl(d["platte_bytes"] / 1e9, 1))
    if d.get("netz_rein") and d.get("netz_raus"):
        sag("spiel.fact.traffic", rein=t.zahl(d["netz_rein"] / 1e9, 1),
            raus=t.zahl(d["netz_raus"] / 1e9, 1))
    if h.get("verbleibend") is not None and h.get("sekunden"):
        sag("spiel.fact.halving_days", n=t.zahl(h["verbleibend"]),
            tage=t.zahl(h["sekunden"] / 86400.0, 0))
    if h.get("epoche") is not None and h.get("subvention_sat") is not None:
        sag("spiel.fact.epoch", n=t.zahl(h["epoche"] + 1),
            btc=t.zahl(h["subvention_sat"] / SAT_JE_BTC, 3))
    if (d.get("schwierigkeit") and d.get("schwierigkeit_start")
            and d["schwierigkeit_start"] > 0):
        v = (d["schwierigkeit"] / d["schwierigkeit_start"] - 1) * 100.0
        sag("spiel.fact.difficulty_since" if v >= 0
            else "spiel.fact.difficulty_since_down", p=t.zahl(abs(v), 0))
    if a.get("verbleibend") is not None:
        sag("spiel.fact.retarget_blocks", n=t.zahl(a["verbleibend"]))

    # Gemischt, damit dieselbe Seite zweimal geladen zwei Zeilen zeigt.
    random.shuffle(z)
    return z


async def fuer_seite(tor, t, z=None):
    """Ein Aufruf, alles fertig fuer die Vorlage.

    Genau eine Zeile in web.py - damit dort nichts ueber den Aufbau dieses
    Bausteins gewusst werden muss.
    """
    d = einordnen(await erhebe(tor, z))
    d["ueberraschungen"] = ueberraschungen(d, t)
    return d


async def puls(tor):
    """Die wenigen Zahlen, die der Browser laufend nachholt.

    Bewusst winzig: getblockchaininfo (9 ms) + getmempoolinfo (7 ms) und, nur
    bei bekannter Hoehe, ein Blockkopf (<6 ms). Ein Block dauert im Mittel
    zehn Minuten; oefter als einmal je Minute zu fragen waere reine Last auf
    einem Knoten, auf dem echtes Geld liegt. Die Seite fragt ausserdem nur,
    solange ihr Reiter sichtbar ist.
    """
    kette, mempool = await asyncio.gather(
        _sicher(tor, "getblockchaininfo"), _sicher(tor, "getmempoolinfo"))
    hoehe = _zahl((kette or {}).get("blocks"))
    zeit = None
    if hoehe is not None:
        zeit = _zahl((await _kopf(tor, int(hoehe)) or {}).get("time"))
    return {
        "hoehe": int(hoehe) if hoehe is not None else None,
        "block_zeit": zeit,
        "mempool_bytes": _zahl((mempool or {}).get("bytes")),
        "mempool_anzahl": _zahl((mempool or {}).get("size")),
        "subvention_sat": subvention_sat(int(hoehe)) if hoehe is not None else None,
    }
