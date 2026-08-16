"""Die Warteschlange, grafisch - alles zum Mempool auf einer eigenen Seite.

QUELLEN und ihre am Knoten gemessenen Kosten:
    getmempoolinfo              7 ms   Anzahl, vsize, Speicher, Grenzen
    mempool.get_fee_histogram   5 ms   (Electrum) die ganze Verteilung
    estimatesmartfee            7 ms   je Ziel einmal, vier Ziele
    estimaterawfee             15 ms   die ungeglaettete Sicht des Schaetzers
Nebenlaeufig erhoben liegt die ganze Seite damit in der Groessenordnung von
30 ms Wandzeit. Nichts davon beruehrt die UTXO-Menge.

⚠️ getrawmempool true waere die naheliegende Quelle und ist VERBOTEN: gemessen
350-450 ms und 13 MB JSON je Aufruf, auf einem Knoten, auf dem echtes Geld
liegt. Das Histogramm des Electrum-Servers sagt dasselbe fuer 5 ms, weil es
genau die Groesse liefert, auf die es ankommt - wieviel vsize haengt an welcher
Gebuehr. Die Liste der einzelnen Transaktionen brauchen wir dafuer nie.

Drei Regeln bestimmen den Aufbau, wie ueberall in diesem Projekt:

* Jeder Wert ist EINZELN abgesichert. Faellt getmempoolinfo aus, fehlt der
  Fuellstand und die Projektion steht trotzdem; faellt Electrum aus, ist es
  umgekehrt. Nie wird etwas geraten, nie faellt wegen einer fehlenden Zahl
  die ganze Seite weg.
* Dieses Modul kennt KEINE Texte. Es liefert Rohwerte, Farben und
  Textschluessel; die Woerter stehen im Katalog (sprache.py).
* Es liefert IMMER denselben Satz Schluessel. Ein fehlender Schluessel wird in
  Jinja zu Undefined, und `Undefined is not none` ist wahr - eine Abfrage in
  der Vorlage liefe also durch und druckte einen leeren Satz.

WAS DAS HISTOGRAMM IST, und was es nicht ist: eine Liste [[Gebuehr, vsize], ...]
nach Gebuehr ABSTEIGEND - genau die Reihenfolge, in der ein Miner einbaut.
Deshalb ist die kumulierte Summe unmittelbar der Platz, der vor einer Gebuehr
liegt, und die ersten 1.000.000 vB sind der naechste Block. Die Gebuehren sind
gebinnt (am .67 gemessen: sieben Stufen fuer 28 MB), die Werte sind also
Stufenkanten und keine Einzelgebuehren. Die Spanne eines kuenftigen Blocks ist
damit grob - aber sie ist gemessen und nicht geschaetzt.
"""
import asyncio

from . import tiefenkarte
from .rpc import RpcFehler

# Ein Block fasst rund 1.000.000 virtuelle Bytes. In dieser Groesse rechnet ein
# Miner, deshalb ist die Warteschlange hier in Bloecke geteilt und nicht in
# Megabyte: "drei Bloecke tief" versteht man, "3,1 MB" nicht.
BLOCK_VBYTE = 1000000
SAT_JE_BTC = 100000000

# Die uebliche Weiterleitungsgrenze. Nur der Rueckfall - der Knoten nennt seine
# eigene in minrelaytxfee, und die zaehlt.
RELAY_BODEN = 1.0

# So viele kuenftige Bloecke zeigen wir hoechstens. Am .67 gemessen liegen
# 26,0 von 28,0 MB unter 1 sat/vB; ohne Deckel stuenden dort 28 Kacheln, von
# denen 25 dasselbe sagen.
ANZEIGE_BLOECKE = 8

# Harte Schleifengrenze. 1000 Bloecke sind 1 GB Warteschlange - mehr als jedes
# maxmempool je zulaesst. Der Deckel ist gegen ein kaputtes Histogramm, nicht
# gegen einen vollen Mempool.
MAX_BLOECKE = 1000

# Bestaetigungsziele fuer estimatesmartfee: naechster Block, halbe Stunde, eine
# Stunde, ein Tag. Dieselben vier wie auf der Startseite, damit die Zahlen
# vergleichbar bleiben und die Textschluessel wiederverwendet werden koennen.
ZIELE = (1, 3, 6, 144)

# Zielabstand zwischen zwei Bloecken, in Minuten. Nur fuer die Beschriftung
# "≈ 20 Min." unter der zweiten Kachel - eine Schaetzung ueber die Zukunft,
# und der Text traegt deshalb ein "≈".
ZIEL_TAKT_MIN = 10


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


def _zahl(wert):
    """Nur echte, endliche Zahlen durchlassen.

    bool ist in Python eine Zahl - hier nicht: `True` als Byteanzahl waere eine
    1, und die saehe man der Anzeige nicht mehr an.
    """
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        return None
    if wert != wert or wert in (float("inf"), float("-inf")):
        return None
    return wert


def _sat_pro_vb(btc_je_kvb):
    """bitcoind rechnet Gebuehren in BTC je kvB. Menschen in sat/vB."""
    wert = _zahl(btc_je_kvb)
    if wert is None or wert <= 0:
        return None
    return wert * SAT_JE_BTC / 1000.0


def _farbe(satz):
    """Gebuehr auf einen Farbton - DIESELBE Skala wie die Tiefenkarte.

    Bewusst geliehen statt kopiert: zwei Tabellen liefen mit der Zeit
    auseinander, und der Leser muesste dann zwei Farbsprachen lernen, obwohl
    beide Ansichten auf derselben Seite untereinanderstehen. Faellt die
    Funktion drueben einmal weg, faerben wir neutral statt abzustuerzen.
    """
    fn = getattr(tiefenkarte, "_farbe", None)
    return fn(satz) if fn else "currentColor"


def _stufen(hist):
    """Histogramm zu [(satz, vsize), ...], absteigend. None, wenn nichts da.

    Einzelne unbrauchbare Eintraege werden uebersprungen, nicht das ganze
    Histogramm verworfen: ein kaputtes Paar kostet dann eine Stufe, nicht die
    Seite. Eine leere Liste ist KEIN Fehler - sie heisst "der Mempool ist
    leer", und das ist eine Aussage.
    """
    if hist is None:
        return None
    stufen = []
    for eintrag in hist:
        try:
            satz, groesse = float(eintrag[0]), float(eintrag[1])
        except (TypeError, ValueError, IndexError):
            continue
        if satz != satz or groesse != groesse:      # NaN
            continue
        if groesse > 0:
            stufen.append((satz, groesse))
    stufen.sort(key=lambda s: s[0], reverse=True)
    return stufen


def _median(teile):
    """Gewichteter Median ueber [(satz, vsize), ...].

    Gewichtet mit der vsize, nicht mit der Anzahl der Stufen: eine Stufe, an
    der 400 kvB haengen, wiegt vierhundertmal so schwer wie eine mit 1 kvB.
    Der ungewichtete Median waere hier schlicht falsch.
    """
    if not teile:
        return None
    geordnet = sorted(teile, key=lambda p: p[0])
    haelfte = sum(g for _, g in geordnet) / 2.0
    kum = 0.0
    for satz, groesse in geordnet:
        kum += groesse
        if kum >= haelfte:
            return satz
    return geordnet[-1][0]


def _unter(satz, boden):
    """Liegt dieser Satz WIRKLICH unter der Weiterleitungsgrenze?

    ⚠️ Die Grenze kommt als 0,00001 BTC/kvB herein und ist nach der Umrechnung
    nicht exakt 1,0, sondern 1,0000000000000002. Ohne diese Toleranz gilt eine
    Stufe mit genau 1 sat/vB als "darunter" - und damit faellt der groesste
    zahlende Posten der Warteschlange (gemessen 1,01 von 28,0 MB) aus der
    Projektion heraus und in den toten Rest hinein.
    """
    return satz < boden * (1.0 - 1e-9)


def _kachel(nr, teile, boden):
    """Ein kuenftiger Block als fertige Kachel."""
    vsize = sum(g for _, g in teile)
    saetze = [s for s, _ in teile]
    median = _median(teile)
    hoechster = max(saetze)
    return {
        "nr": nr,
        "vsize": int(vsize),
        # Der letzte projizierte Block ist meist nur angebrochen. Genau das
        # ist die Aussage der Kachel: soviel Platz waere noch frei.
        "fuellung_p": max(0.0, min(100.0, vsize * 100.0 / BLOCK_VBYTE)),
        "voll": vsize >= BLOCK_VBYTE,
        "min": min(saetze),
        "max": hoechster,
        "median": median,
        "farbe": _farbe(median),
        # Wartezeit bis zu diesem Block, bei Zieltakt. Ausdruecklich eine
        # Schaetzung ueber die Zukunft; die Vorlage schreibt ein "≈" davor.
        "minuten": nr * ZIEL_TAKT_MIN,
        # Unterhalb der Weiterleitungsgrenze wird dieser Block von keinem
        # Knoten mehr weitergereicht - die Kachel sagt das statt einer Spanne.
        "unter_boden": _unter(hoechster, boden),
        # Der innere Aufbau als gestapelter Balken: welche Gebuehren machen
        # diesen Block aus? Absteigend, also in Einbaureihenfolge.
        "segmente": [{"satz": s, "vsize": int(g), "farbe": _farbe(s)}
                     for s, g in teile if g > 0],
    }


# --------------------------------------------------------------- Projektion
def projektion(hist, boden=RELAY_BODEN, hoechstens=ANZEIGE_BLOECKE):
    """Die naechsten Bloecke, wie ein Miner sie bauen wuerde. None ohne Daten.

    Der Miner nimmt die teuerste Transaktion zuerst und hoert auf, wenn
    1.000.000 vB voll sind. Genau das rechnet diese Funktion nach: das
    Histogramm wird in 1-MvB-Schritte geschnitten, jede Stufe darf dabei ueber
    eine Blockgrenze hinweg geteilt werden.

    Reine Rechnung, kein Netz - damit genau dieser Teil ohne Knoten und ohne
    Electrum-Server pruefbar ist.
    """
    stufen = _stufen(hist)
    if stufen is None:
        return None

    gesamt = sum(g for _, g in stufen)
    # Was unter der Weiterleitungsgrenze liegt, bewegt sich nicht mehr: kein
    # Knoten reicht es weiter, kein Miner baut es ein. Am .67 gemessen sind
    # das 26,0 von 28,0 MB - ohne diese Zahl waere die Warteschlange
    # unverstaendlich lang.
    tot = sum(g for s, g in stufen if _unter(s, boden))

    bloecke, laufend, gefuellt = [], [], 0.0
    for satz, groesse in stufen:
        if len(bloecke) >= MAX_BLOECKE:
            break
        rest = groesse
        while rest > 0 and len(bloecke) < MAX_BLOECKE:
            platz = BLOCK_VBYTE - gefuellt
            nimm = rest if rest < platz else platz
            laufend.append((satz, nimm))
            gefuellt += nimm
            rest -= nimm
            if gefuellt >= BLOCK_VBYTE:
                bloecke.append(_kachel(len(bloecke) + 1, laufend, boden))
                laufend, gefuellt = [], 0.0
    if laufend and len(bloecke) < MAX_BLOECKE:
        bloecke.append(_kachel(len(bloecke) + 1, laufend, boden))

    # Gezeigt werden nur die Bloecke, in denen ueberhaupt noch etwas zahlt.
    # Die Saetze fallen monoton, die zahlenden Bloecke sind also immer der
    # Anfang der Liste - kein Aussortieren mitten drin.
    zahlend = [b for b in bloecke if not b["unter_boden"]]
    zeige = zahlend[:hoechstens] or bloecke[:1]

    # Der Schwanz: alles, was nicht mehr als eigene Kachel gezeigt wird.
    # Bewusst NICHT "rest" genannt - so heisst die Laufvariable der Schleife
    # oben, und zwei Bedeutungen fuer einen Namen liest niemand richtig.
    uebrig = gesamt - sum(b["vsize"] for b in zeige)
    schwanz = None
    if uebrig > 0 and len(bloecke) > len(zeige):
        naechster = bloecke[len(zeige)]
        schwanz = {
            "vsize": int(uebrig),
            # Aufgerundet: ein angebrochener Block ist ein wartender Block.
            "bloecke": int((uebrig + BLOCK_VBYTE - 1) // BLOCK_VBYTE),
            "hoechster": naechster["max"],
            "unter_boden": naechster["unter_boden"],
        }

    voll = gesamt >= BLOCK_VBYTE
    return {
        "bloecke": zeige,
        "rest": schwanz,
        "gesamt_vsize": int(gesamt),
        "gesamt_bloecke": gesamt / BLOCK_VBYTE,
        # Die Aufnahmeschwelle: der Satz an der Stelle, an der die kumulierte
        # vsize 1.000.000 erreicht. Wer darunter bietet, ist im naechsten
        # Block nicht dabei. Sie gibt es nur, wenn ueberhaupt ein Block voll
        # wird - sonst waere sie eine Behauptung.
        "schwelle": bloecke[0]["min"] if (voll and bloecke) else None,
        "passt_alles": not voll,
        "frei_vsize": int(BLOCK_VBYTE - gesamt) if not voll else None,
        "hoechster": stufen[0][0] if stufen else None,
        "tot_vsize": int(tot),
        "tot_p": (tot * 100.0 / gesamt) if gesamt else None,
        "boden": boden,
        "leer": not stufen,
    }


# --------------------------------------------------------------- Fuellstand
def fuellstand(info):
    """Belegter Speicher gegen maxmempool - und ob der Knoten wegwirft.

    ⚠️ Der Fuellstand haengt an `usage`, nicht an `bytes`: geworfen wird nach
    Arbeitsspeicher, und der ist ein Vielfaches der virtuellen Groesse (am
    Probeknoten 142 MB Speicher fuer 28 MB vsize). Wer hier `bytes` gegen
    `maxmempool` haelt, sieht 9 % Fuellung, waehrend der Knoten schon raeumt.

    Liefert IMMER alle Schluessel - siehe Modulkopf.
    """
    i = info if isinstance(info, dict) else {}
    speicher = _zahl(i.get("usage"))
    grenze = _zahl(i.get("maxmempool"))
    vbytes = _zahl(i.get("bytes"))
    min_sat_vb = _sat_pro_vb(i.get("mempoolminfee"))
    relay_sat_vb = _sat_pro_vb(i.get("minrelaytxfee"))
    gebuehr = _zahl(i.get("total_fee"))

    anteil = (speicher * 100.0 / grenze) if (speicher is not None and grenze) else None
    # ⚠️ Die Toleranz ist noetig: 0,00001 BTC/kvB sind nach der Umrechnung
    # nicht exakt 1,0, sondern 1,0000000000000002. Ohne Luft nach oben stuende
    # auf jeder ruhigen Seite die Ueberlaufwarnung.
    ueberlauf = bool(min_sat_vb and relay_sat_vb and min_sat_vb > relay_sat_vb * 1.01)

    return {
        "anzahl": _zahl(i.get("size")),
        "vbytes": vbytes,
        "tiefe_bloecke": (vbytes / BLOCK_VBYTE) if vbytes is not None else None,
        "speicher_bytes": speicher,
        "max_bytes": grenze,
        "speicher_p": anteil,
        "speicher_klasse": _klasse(anteil),
        "min_sat_vb": min_sat_vb,
        "relay_sat_vb": relay_sat_vb,
        "ueberlauf": ueberlauf,
        # Um wieviel die Aufnahmegrenze ueber die Weiterleitungsgrenze
        # gestiegen ist. Nur dann eine Aussage, wenn ueberhaupt geraeumt wird.
        "ueberlauf_faktor": (min_sat_vb / relay_sat_vb) if ueberlauf else None,
        # `is not None`, nicht `if gebuehr`: eine leere Warteschlange hat
        # wirklich 0 BTC an Gebuehren, und diese Null ist ein Messwert. Ein
        # Strich stuende dort faelschlich fuer "unbekannt".
        "gebuehr_sat": (int(round(gebuehr * SAT_JE_BTC))
                        if gebuehr is not None else None),
        "geladen": i.get("loaded") is not False,
    }


def _klasse(anteil):
    """Vier Stufen fuer die Farbe des Balkens. Grober waere blind, feiner
    Geraeusch: zwischen 71 und 74 Prozent liegt keine Entscheidung."""
    if anteil is None:
        return None
    if anteil >= 95.0:
        return "voll"
    if anteil >= 80.0:
        return "eng"
    if anteil >= 50.0:
        return "halb"
    return "leer"


# --------------------------------------------------------------- Schaetzer
async def _schaetzungen(tor):
    """estimatesmartfee fuer alle Ziele, jedes einzeln abgesichert.

    Bei zu duenner Datenlage antwortet bitcoind mit "errors" statt "feerate".
    Dann fehlt genau dieser Balken, nicht die ganze Reihe.
    """
    roh = await asyncio.gather(*[_sicher(tor, "estimatesmartfee", n)
                                 for n in ZIELE])
    raus = []
    for ziel, e in zip(ZIELE, roh):
        rate = _sat_pro_vb((e or {}).get("feerate"))
        raus.append({
            "ziel": ziel,
            # Der Textschluessel wandert mit dem Wert - dieselben vier wie auf
            # der Startseite, damit die Vorlage keine eigene Tabelle fuehrt.
            "schluessel": "spiel.fee.target%d" % ziel,
            "sat_vb": rate,
            "anteil_p": None,
        })
    hoechste = max([g["sat_vb"] for g in raus if g["sat_vb"]] or [0])
    for g in raus:
        # Massstab ist das teuerste Ziel. Eine absolute Skala waere bei
        # 1-2 sat/vB nicht mehr zu unterscheiden.
        if g["sat_vb"] and hoechste:
            g["anteil_p"] = max(2.0, g["sat_vb"] * 100.0 / hoechste)
    return raus


def _rohgebuehr(roh):
    """estimaterawfee: die ungeglaettete Sicht des Schaetzers.

    estimatesmartfee glaettet und rundet auf; estimaterawfee zeigt, was der
    kurze Horizont wirklich gemessen hat. Die Differenz sagt, wieviel
    Sicherheitsabstand die Empfehlung haelt.
    """
    kurz = (roh or {}).get("short")
    if not isinstance(kurz, dict):
        return None
    return _sat_pro_vb(kurz.get("feerate"))


# --------------------------------------------------------------- Hauptzugang
def _leer():
    """Der vollstaendige Schluesselsatz, alles unbekannt. Siehe Modulkopf:
    ein fehlender Schluessel ist in Jinja Undefined und laeuft durch."""
    d = dict.fromkeys((
        "anzahl", "vbytes", "tiefe_bloecke", "speicher_bytes", "max_bytes",
        "speicher_p", "speicher_klasse", "min_sat_vb", "relay_sat_vb",
        "ueberlauf_faktor", "gebuehr_sat", "projektion", "karte",
        "schaetzungen", "roh_sat_vb", "schaetzung_naechster", "abweichung_p"))
    d["ueberlauf"] = False
    d["geladen"] = True
    d["erreichbar"] = False
    d["histogramm_da"] = False
    return d


async def seite(tor, zeitlimit=6.0):
    """Alles, was mempool.html braucht - in einem Aufruf, alles nebenlaeufig.

    Vier Quellen gleichzeitig statt nacheinander: die Seite wartet damit auf
    die langsamste, nicht auf die Summe. Faellt eine aus, fehlt genau ihr
    Teil - der Knoten kann stumm sein und das Histogramm trotzdem stehen.
    """
    info, hist, schaetzungen, roh = await asyncio.gather(
        _sicher(tor, "getmempoolinfo"),
        tiefenkarte.histogramm(zeitlimit),
        _schaetzungen(tor),
        _sicher(tor, "estimaterawfee", 1),
    )

    d = _leer()
    d.update(fuellstand(info))
    d["erreichbar"] = info is not None

    # Der Boden kommt vom Knoten, wenn er antwortet - eine fest verdrahtete
    # 1 sat/vB waere auf einem Knoten mit eigener minrelaytxfee schlicht falsch.
    boden = d["relay_sat_vb"] or RELAY_BODEN
    p = projektion(hist, boden)
    d["projektion"] = p
    d["histogramm_da"] = p is not None
    # Die Tiefenkarte zeichnet dasselbe Histogramm als Treppe. Sie wird hier
    # gleich mitgerechnet, damit die Vorlage nur EINE Quelle kennt und die
    # Seite nicht zweimal beim Electrum-Server anklopft.
    d["karte"] = tiefenkarte.karte(hist)

    # Die Tiefe der Schlange sagt der Knoten selbst (getmempoolinfo.bytes).
    # Schweigt er, nehmen wir die Summe des Histogramms - dieselbe Groesse,
    # nur vom Electrum-Server gezaehlt. Das ist kein Schaetzen: beide messen
    # virtuelle Bytes, sie koennen nur um die Sekunden auseinanderliegen, die
    # zwischen ihren Staenden liegen.
    if d["tiefe_bloecke"] is None and p:
        d["tiefe_bloecke"] = p["gesamt_bloecke"]

    d["schaetzungen"] = schaetzungen
    d["roh_sat_vb"] = _rohgebuehr(roh)
    d["schaetzung_naechster"] = next(
        (g["sat_vb"] for g in schaetzungen or [] if g.get("ziel") == 1), None)

    # Der Schaetzer des Knotens gegen die gemessene Warteschlange. Beide
    # beantworten dieselbe Frage auf zwei Wegen; wo sie auseinanderliegen,
    # ist das die interessanteste Zahl der Seite.
    schwelle = (p or {}).get("schwelle")
    if schwelle and d["schaetzung_naechster"]:
        abweichung = (d["schaetzung_naechster"] / schwelle - 1.0) * 100.0
        # Unter fuenf Prozent ist der Unterschied Rauschen - beide Zahlen sind
        # gerundete Stufenkanten. Gemessen kommt hier sonst 0,00000000000002 %
        # heraus und stuende als "+0 %" auf der Seite.
        d["abweichung_p"] = abweichung if abs(abweichung) >= 5.0 else None
    return d
