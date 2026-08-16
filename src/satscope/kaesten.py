"""Die vier Kaesten unter dem Blockband.

Im Vorbild stehen unter dem waagerechten Band vier Kaesten. Drei davon bauen
wir nach, den vierten ersetzen wir - und zwar nicht aus Bequemlichkeit:

    1. Transaktionsgebuehren   estimatesmartfee 1/3/6/144 + mempoolminfee
    2. Schwierigkeitsanpassung Fortschritt, Aenderung, Datum, Takt
    3. Letzte Bloecke          Hoehe, Miner-Zeit, Anzahl, Groesse
    4. Ausgabe & Halbierung    an der Stelle, wo im Vorbild die zuletzt
                               gesehenen Transaktionen stehen

⚠️ WARUM KEIN TRANSAKTIONSSTROM. Die "letzten Transaktionen" im Vorbild kommen
aus getrawmempool oder aus ZMQ. getrawmempool true ist in diesem Projekt
verboten (gemessen 350-450 ms und 13 MB JSON je Aufruf auf einem Knoten, auf
dem echtes Geld liegt), ZMQ steht uns nicht zur Verfuegung. Beides waere fuer
eine Kachel, die man ohnehin nur ueberfliegt, ein hoher Preis. Statt einen
Strom zu erfinden oder eine gesperrte Methode aufzurufen, steht dort die
zweite Uhr des Protokolls: die Halbierung. Sie kostet KEINEN einzigen
zusaetzlichen Aufruf (reine Arithmetik aus der Hoehe), sie ist bis auf das
geschaetzte Datum exakt, und sie ergaenzt Kasten 2 - die eine Uhr stellt den
Takt, die andere die Ausgabe.

KOSTEN. Der Regelfall auf der Startseite ist NULL zusaetzliche RPC-Aufrufe:
die Bloecke kommen aus dem Speicher von kette.py (den das Band ohnehin fuellt),
die Gebuehren und die Anpassung aus dem bereits erhobenen Spielsatz
(spiel.fuer_seite). Fehlt der Spielsatz, holt dieses Modul selbst nach - dann
sind es hoechstens vier estimatesmartfee (7 ms), ein getmempoolinfo (7 ms) und
drei Blockkoepfe (<6 ms). Nichts davon beruehrt die UTXO-Menge, nichts davon
steht auf der teuren Liste.

WIR RECHNEN DIE ANPASSUNG NICHT NACH. spiel.py hat die Formel schon - samt der
beiden Feinheiten, die man leicht falsch macht (der Konsens-Deckel auf Faktor 4
bzw. 1/4 und die bekannte Abweichung um einen Block). Eine zweite Fassung
derselben Rechnung liefe irgendwann auseinander, und dann stuenden auf zwei
Seiten desselben Programms zwei verschiedene Prozentzahlen. Dasselbe gilt fuer
die Halbierung (ganzzahlige Schiebung statt Fliesskomma). Beide Funktionen sind
drueben privat; sie werden deshalb ueber getattr geholt: verschwindet eine bei
einem Umbau, zeigt der Kasten Striche, statt die Startseite abzuschiessen.

Und die drei Regeln des Projekts, hier wie ueberall:

* Jeder Wert ist EINZELN abgesichert. Faellt ein Aufruf aus, ist genau dieser
  Wert None und die Vorlage zeigt dort einen Strich. Geraten wird nichts.
* Dieses Modul kennt KEINE Texte. Es liefert Rohwerte und Textschluessel; die
  Woerter stehen im Katalog (sprache.py).
* Es liefert IMMER denselben Satz Schluessel. Ein fehlender Schluessel wird in
  Jinja zu Undefined, und `Undefined is not none` ist wahr - eine Abfrage in
  der Vorlage liefe also durch und druckte einen leeren Satz.
"""
import asyncio
import time

from . import kette, spiel
from .rpc import RpcFehler

# Bestaetigungsziele: naechster Block, halbe Stunde, eine Stunde, ein Tag.
# Dieselben vier wie in spiel.py und mempoolseite.py - so bleiben die Zahlen
# ueber die Seiten hinweg vergleichbar und die Textschluessel wiederverwendbar.
ZIELE = (1, 3, 6, 144)

# Die vier Stufen des Vorbilds, in seiner Reihenfolge (links keine, rechts
# hohe Prioritaet), und woher ihre Zahl kommt.
#
# ⚠️ "Keine Prioritaet" ist bewusst KEINE Schaetzung, sondern die
# Weiterleitungsgrenze des eigenen Knotens (getmempoolinfo.mempoolminfee):
# darunter nimmt er eine Transaktion gar nicht erst an, sie kaeme also nie
# irgendwo an. Das ist die einzige Zahl, die an dieser Stelle wirklich
# "unterste Kante" heisst - eine Schaetzung fuer 144 Bloecke waere etwas
# anderes und steht deshalb als eigene Zeile unter den Kacheln.
STUFEN = (
    ("kaesten.fees.none", None),
    ("fee.low", 6),
    ("fee.medium", 3),
    ("fee.high", 1),
)

# Eine gewoehnliche Zahlung: ein Eingang, zwei Ausgaenge, P2WPKH. Aus spiel.py
# geliehen, damit "was kostet das" auf beiden Seiten dieselbe Musterzahlung
# meint - zwei verschiedene Grundgroessen waeren zwei verschiedene Antworten
# auf dieselbe Frage.
TYPISCHE_VB = getattr(spiel, "TYPISCHE_VB", 141)

SAT_JE_BTC = 100000000

# Konsensgrenze: 4.000.000 Gewichtseinheiten je Block. Die Fuellung wird daran
# gemessen und NICHT an einem Megabyte - seit SegWit waere das die falsche
# Grenze.
BLOCKGEWICHT = 4000000

# So viele Zeilen zeigt die Blocktabelle. Sechs, weil das rund eine Stunde
# Kette ist und der Kasten damit ungefaehr so hoch wird wie der daneben.
LETZTE = 6

# Aus spiel.py, damit die Konstanten nur an EINER Stelle stehen.
ZIEL_TAKT = getattr(spiel, "ZIEL_TAKT", 600)
PERIODE = getattr(spiel, "PERIODE", 2016)

# Bloecke je Tag im Zieltakt. Nur fuer "so viele Bitcoin entstehen am Tag" -
# ausdruecklich im Zieltakt gerechnet, nicht im gemessenen: der Text sagt das.
TAG_BLOECKE = 86400 // ZIEL_TAKT

# Unter dieser Ersparnis lohnt der Hinweis "warte einen Tag" nicht - fuenf
# Prozent auf eine Zahlung sind ein paar Satoshi.
SPARSCHWELLE = 5.0

# ⚠️ Die Weiterleitungsgrenze kommt als 0,00001 BTC/kvB herein und ist nach der
# Umrechnung nicht exakt 1,0, sondern 1,0000000000000002. Ohne diese Toleranz
# stuende auf jeder ruhigen Seite die Warnung "dein Knoten nimmt nichts unter
# 1,000 sat/vB an".
BODEN_TOLERANZ = 1.01

# Erst ab dieser Abweichung ist "der Plan" eine Nachricht. Eine Viertelstunde
# Vorsprung auf zwei Wochen ist Rauschen.
VORSPRUNG_MIN = 3600.0

# Der volle Schluesselsatz der beiden geliehenen Rechnungen. Er steht hier
# noch einmal, damit ein leerer Kasten dieselben Felder hat wie ein gefuellter
# (siehe Modulkopf: fehlende Schluessel sind in Jinja Undefined).
ANPASSUNG_FELDER = ("verbleibend", "fortschritt_p", "aenderung_p", "takt_s",
                    "sekunden", "zeitpunkt", "im_zeitraum", "neu",
                    "eta_schluessel", "eta_wert")
HALBIERUNG_FELDER = ("epoche", "hoehe", "verbleibend", "fortschritt_p",
                     "sekunden", "zeitpunkt", "subvention_sat", "naechste_sat",
                     "eta_schluessel", "eta_wert")


# ------------------------------------------------------------ Werkzeug
async def _sicher(tor, methode, *argumente):
    """Ruft auf und liefert None statt zu werfen.

    Absichtlich eine eigene Kopie und kein Import eines privaten Namens aus
    einem Nachbarmodul: der Strich statt einer erfundenen Zahl ist die
    wichtigste Regel dieses Projekts und soll in jedem Modul sichtbar
    dastehen, nicht hinter einem Import verschwinden.
    """
    try:
        return await tor.ruf(methode, *argumente)
    except (RpcFehler, OSError, asyncio.TimeoutError):
        return None


def _zahl(wert):
    """Nur echte, endliche Zahlen durchlassen.

    bool ist in Python eine Zahl - hier nicht: `True` als Blockhoehe waere
    eine 1, und das saehe man der Anzeige nicht mehr an.
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


def _utc(stempel, form="%Y-%m-%d %H:%M"):
    """Zeitstempel als UTC-Text, oder None.

    Keine Ortszeit: der Server kennt die Zeitzone des Browsers nicht, und eine
    falsche Zeitzone ist schlimmer als eine fremde. Dieselbe Entscheidung wie
    in blockliste.uhrzeit - dort steht sie fuer die Blockliste, hier fuer die
    Kaesten; ein Import quer durch die Module waere fuer drei Zeilen strftime
    eine Fessel.
    """
    wert = _zahl(stempel)
    if wert is None:
        return None
    return time.strftime(form, time.gmtime(int(wert))) + " UTC"


async def _kopf(tor, hoehe=None, hasch=None):
    """Blockkopf ueber Hoehe ODER Hash. Beides sind die billigsten Aufrufe des
    Knotens (getblockhash <1 ms, getblockheader <5 ms).

    Ueber den Hash, wo einer dasteht: das spart nicht nur einen Aufruf, es ist
    auch das genauere Vorgehen - faellt zwischen zwei Aufrufen eine
    Reorganisation, antwortet der Knoten auf den verwaisten Hash gar nicht,
    waehrend die Hoehe stillschweigend einem anderen Block gehoerte.
    """
    if hasch is None:
        if hoehe is None or hoehe < 0:
            return None
        hasch = await _sicher(tor, "getblockhash", int(hoehe))
        if not isinstance(hasch, str):
            return None
    return await _sicher(tor, "getblockheader", hasch)


# ------------------------------------------------------ 1. Gebuehrenkasten
async def _gebuehren(tor, s, k):
    """Die vier Stufen des Vorbilds, jede einzeln abgesichert.

    Drei Quellen in dieser Reihenfolge, damit der Regelfall nichts kostet:
    der bereits erhobene Spielsatz, der Zwischenspeicher der Blockkette (er
    fuehrt die Ziele 1/3/6 ohnehin mit) und erst danach eigene Aufrufe fuer
    das, was dann noch fehlt.
    """
    saetze = dict.fromkeys(ZIELE)

    for g in (s or {}).get("gebuehren") or []:
        ziel = (g or {}).get("ziel")
        if ziel in saetze and saetze[ziel] is None:
            saetze[ziel] = _zahl(g.get("sat_vb"))

    m = (k or {}).get("mempool") or {}
    for e in m.get("schaetzung") or []:
        ziel = (e or {}).get("ziel")
        if ziel in saetze and saetze[ziel] is None:
            saetze[ziel] = _zahl(e.get("satvb"))

    boden = _zahl((s or {}).get("mempool_min_sat_vb"))
    if boden is None:
        boden = _zahl(m.get("min_gebuehr"))

    # Nur nachholen, was wirklich fehlt. Bei zu duenner Datenlage antwortet
    # bitcoind mit "errors" statt "feerate" - dann fehlt genau diese Kachel,
    # nicht der ganze Kasten.
    fehlend = [z for z in ZIELE if saetze[z] is None]
    if fehlend:
        antworten = await asyncio.gather(
            *[_sicher(tor, "estimatesmartfee", z) for z in fehlend])
        for ziel, antwort in zip(fehlend, antworten):
            saetze[ziel] = _sat_pro_vb((antwort or {}).get("feerate"))
    if boden is None:
        info = await _sicher(tor, "getmempoolinfo")
        boden = _sat_pro_vb((info or {}).get("mempoolminfee"))

    stufen = []
    for schluessel, ziel in STUFEN:
        satz = boden if ziel is None else saetze.get(ziel)
        stufen.append({
            "schluessel": schluessel,
            "ziel": ziel,
            # Der Textschluessel des Ziels wandert mit dem Wert, damit die
            # Vorlage keine eigene Tabelle Ziel -> Wort fuehren muss.
            "ziel_schluessel": ("kaesten.fees.relay" if ziel is None
                                else "spiel.fee.target%d" % ziel),
            "sat_vb": satz,
            # Was eine gewoehnliche Zahlung damit kostet - in Satoshi.
            # ⚠️ KEIN Gegenwert in Euro oder Dollar: dafuer braeuchte es eine
            # Kursquelle im Internet, und dieses Programm ruft nie hinaus.
            "kosten_sat": (int(round(satz * TYPISCHE_VB))
                           if satz is not None else None),
        })

    schnell, geduldig = saetze.get(1), saetze.get(144)
    sparen = None
    if schnell and geduldig and geduldig < schnell:
        ersparnis = (1.0 - geduldig / schnell) * 100.0
        if ersparnis >= SPARSCHWELLE:
            sparen = ersparnis

    return {
        "stufen": stufen,
        "boden": boden,
        # Die eigene Untergrenze ist nur dann eine Nachricht, wenn sie ueber
        # der ueblichen 1 sat/vB liegt - dann raeumt der Knoten gerade auf.
        "boden_warnung": (boden if (boden and boden > BODEN_TOLERANZ)
                          else None),
        "tag_sat_vb": geduldig,
        "sparen_p": sparen,
        "typisch_vb": TYPISCHE_VB,
        "da": any(x["sat_vb"] is not None for x in stufen),
    }


# ------------------------------------------------- 2. Schwierigkeitskasten
def _leere_anpassung():
    return dict.fromkeys(ANPASSUNG_FELDER)


async def _anpassung_selbst(tor, k, hoehe):
    """Die drei billigen Zahlen holen und spiel.py rechnen lassen.

    Gebraucht werden nur: die Zeit der Spitze, die Zeit des ersten Blocks der
    laufenden Periode und die aktuelle Schwierigkeit. Alle drei stehen in
    Blockkoepfen - zusammen unter 20 ms.
    """
    rechne = getattr(spiel, "_anpassung", None)
    if hoehe is None or rechne is None:
        return _leere_anpassung(), None

    start = (int(hoehe) // PERIODE) * PERIODE
    spitzen_hash = (k or {}).get("spitze_hash")
    kopf_spitze, kopf_start = await asyncio.gather(
        _kopf(tor, hoehe=hoehe, hasch=spitzen_hash if spitzen_hash else None),
        _kopf(tor, hoehe=start))

    # Die Zeit der Spitze steht meist schon im Band - dann ist der Blockkopf
    # nur noch fuer die Schwierigkeit da.
    block_zeit = None
    for b in reversed((k or {}).get("bloecke") or []):
        block_zeit = _zahl(b.get("zeit"))
        if block_zeit is not None:
            break
    if block_zeit is None:
        block_zeit = _zahl((kopf_spitze or {}).get("time"))

    schwierigkeit = _zahl((kopf_spitze or {}).get("difficulty"))
    # Der gemessene Takt der letzten Bloecke - fuer den ZEITPUNKT, nicht fuer
    # die Prozente. Fehlt er, nimmt spiel.py den Takt der laufenden Periode.
    takt = _zahl(((k or {}).get("fenster") or {}).get("abstand_schnitt"))
    return rechne(int(hoehe), block_zeit, kopf_start, takt,
                  schwierigkeit), schwierigkeit


def _anpassung_schmuecken(a, schwierigkeit):
    """Die Rechnung von spiel.py um das ergaenzen, was nur dieser Kasten zeigt.

    Nichts davon rechnet die Anpassung nach - es sind ausschliesslich Formen
    derselben Zahlen: eine Richtung, ein Datum, der Vorsprung auf den Plan.
    """
    d = dict(_leere_anpassung())
    d.update({feld: (a or {}).get(feld) for feld in ANPASSUNG_FELDER})

    aenderung = _zahl(d.get("aenderung_p"))
    # ⚠️ Die Richtung wird bewusst NICHT gruen oder rot gefaerbt (dieselbe
    # Entscheidung wie in spiel.einordnen): steigende Schwierigkeit heisst
    # "es wurde schneller gemined", fallende "langsamer" - beides ist weder
    # gut noch schlecht, sondern genau der Regelkreis, der Bitcoin bei zehn
    # Minuten haelt.
    d["richtung"] = (None if aenderung is None
                     else "hoch" if aenderung > 0
                     else "runter" if aenderung < 0 else "gleich")
    d["aenderung_schluessel"] = (
        None if aenderung is None
        else "kaesten.change.up" if aenderung > 0
        else "kaesten.change.down" if aenderung < 0
        else "kaesten.change.flat")
    # Fuer die Anzeige der Betrag: das Vorzeichen steckt schon im Text.
    d["aenderung_betrag"] = abs(aenderung) if aenderung is not None else None
    # ⚠️ "Noch zu frueh in dieser Periode" darf NUR dastehen, wenn wir die
    # Periode ueberhaupt kennen. Bei einem stummen Knoten waere derselbe Satz
    # eine Behauptung ueber etwas, das gar nicht gemessen wurde - dann bleibt
    # die Zeile leer und die Striche sprechen fuer sich.
    d["satz_schluessel"] = (
        None if d.get("verbleibend") is None
        else "spiel.retarget.unknown" if aenderung is None
        else "spiel.retarget.up" if aenderung > 0
        else "spiel.retarget.down" if aenderung < 0
        else "spiel.retarget.flat")

    d["datum"] = _utc(d.get("zeitpunkt"))

    # Vorsprung auf den Plan: die Periode SOLL im Zehn-Minuten-Takt laufen.
    # Reine Arithmetik aus dem Periodentakt, den spiel.py schon geliefert hat.
    im_zeitraum = _zahl(d.get("im_zeitraum"))
    takt = _zahl(d.get("takt_s"))
    d["vorsprung_s"] = None
    d["vorsprung_schluessel"] = None
    if im_zeitraum and takt:
        vorsprung = im_zeitraum * (ZIEL_TAKT - takt)
        if abs(vorsprung) >= VORSPRUNG_MIN:
            d["vorsprung_s"] = abs(vorsprung)
            d["vorsprung_schluessel"] = ("kaesten.retarget.ahead"
                                         if vorsprung > 0
                                         else "kaesten.retarget.behind")

    # Schwierigkeiten sind vierzehnstellig. In Tera gelesen sind sie
    # vergleichbar, so wie sie ueberall sonst auch angeschrieben stehen.
    neu = _zahl(d.get("neu"))
    d["neu_t"] = neu / 1e12 if neu is not None else None
    d["jetzt_t"] = (schwierigkeit / 1e12
                    if _zahl(schwierigkeit) is not None else None)
    d["periode"] = PERIODE
    return d


async def _anpassung(tor, s, k, hoehe):
    """Die Anpassung aus dem Spielsatz - oder selbst erhoben."""
    a = (s or {}).get("anpassung")
    if isinstance(a, dict) and a.get("verbleibend") is not None:
        return _anpassung_schmuecken(a, _zahl((s or {}).get("schwierigkeit")))
    a, schwierigkeit = await _anpassung_selbst(tor, k, hoehe)
    return _anpassung_schmuecken(a, schwierigkeit)


# ---------------------------------------------------- 3. Kasten der Bloecke
def _bloecke(k):
    """Die letzten Bloecke als Tabellenzeilen, NEUESTER ZUERST.

    Die Quelle ist der Zwischenspeicher aus kette.py - dieselben Zahlen, die
    das Band ueber dieser Tabelle zeichnet. Zwei getrennte Erhebungen fuer
    dieselben Bloecke waeren doppelte Last auf dem Knoten und, schlimmer, zwei
    Staende, die um Sekunden auseinanderliegen.

    ⚠️ Die Zeit ist die des MINERS (der Zeitstempel im Blockkopf), nicht die
    des Empfangs. Sie ist im Konsens nicht streng steigend - ein Miner darf
    leicht zurueckdatieren. Genau deshalb steht hier eine Uhrzeit und kein
    "vor n Minuten": eine feste Uhrzeit veraltet nicht, waehrend eine
    serverseitig gerechnete Altersangabe schon beim Lesen falsch ist (ohne
    JavaScript laeuft sie nicht mit).
    """
    zeilen = []
    for b in reversed(list((k or {}).get("bloecke") or [])[-LETZTE:]):
        zeit = _zahl(b.get("zeit"))
        gewicht = _zahl(b.get("gewicht"))
        groesse = _zahl(b.get("groesse"))
        zeilen.append({
            "hoehe": _zahl(b.get("hoehe")),
            "zeit": zeit,
            "zeit_kurz": _utc(zeit, "%H:%M"),
            "zeit_voll": _utc(zeit, "%Y-%m-%d %H:%M:%S"),
            "txs": _zahl(b.get("txs")),
            "groesse_mb": groesse / 1e6 if groesse is not None else None,
            "fuellung_p": (min(100.0, gewicht * 100.0 / BLOCKGEWICHT)
                           if gewicht else None),
        })
    return zeilen


# ------------------------------------------------------ 4. Halbierungskasten
def _leere_halbierung():
    return dict.fromkeys(HALBIERUNG_FELDER)


def _halbierung(s, hoehe):
    """Der Zaehler bis zur naechsten Halbierung.

    Kostet keinen einzigen Aufruf: alles folgt aus der Hoehe. Gerechnet wird
    er in spiel.py (ganzzahlige Schiebung, damit bei Geldbetraegen kein
    Rundungsfehler entsteht) - hier kommen nur die Formen dazu.
    """
    h = (s or {}).get("halbierung")
    if not (isinstance(h, dict) and h.get("verbleibend") is not None):
        rechne = getattr(spiel, "_halbierung", None)
        h = rechne(int(hoehe)) if (rechne and hoehe is not None) else None

    d = dict(_leere_halbierung())
    d.update({feld: (h or {}).get(feld) for feld in HALBIERUNG_FELDER})

    subvention = _zahl(d.get("subvention_sat"))
    naechste = _zahl(d.get("naechste_sat"))
    d["btc"] = subvention / SAT_JE_BTC if subvention is not None else None
    d["naechste_btc"] = naechste / SAT_JE_BTC if naechste is not None else None
    # Wieviel neu entsteht, wenn das Netz im Zieltakt laeuft. Ausdruecklich
    # im Zieltakt - der Text sagt das mit einem "≈".
    d["pro_tag_btc"] = (subvention * TAG_BLOECKE / SAT_JE_BTC
                        if subvention is not None else None)
    # Nur das Datum, keine Uhrzeit: bis zur naechsten Halbierung sind es
    # Jahre, eine Minutenangabe waere eine Genauigkeit, die es nicht gibt.
    d["datum"] = _utc(d.get("zeitpunkt"), "%Y-%m-%d")
    epoche = _zahl(d.get("epoche"))
    # Der Zaehler in den Texten ist die laufende Epoche, und die beginnt bei
    # eins - `epoche` zaehlt ab null.
    d["epoche_nr"] = int(epoche) + 1 if epoche is not None else None
    return d


# ------------------------------------------------------------ Hauptzugang
async def erhebe(tor, s=None, k=None):
    """Alle vier Kaesten in einem Aufruf.

        ka = await kaesten.erhebe(TOR, s)

    `s` ist der bereits erhobene Spielsatz aus spiel.fuer_seite(), `k` die
    bereits geholten Kettendaten aus kette.kette(). Beide sind freiwillig:
    wer sie durchreicht, spart die Aufrufe (die Startseite hat den Spielsatz
    ohnehin), wer sie weglaesst, bekommt dieselbe Antwort etwas teurer.
    """
    if k is None:
        # Der Speicher in kette.py ist der Grund, warum das hier keine Last
        # ist: das Band fragt dieselbe Quelle alle fuenf Sekunden, erhoben
        # wird sie nur bei einem neuen Spitzenblock.
        k = await kette.kette(tor)

    hoehe = _zahl((k or {}).get("spitze"))
    if hoehe is None:
        hoehe = _zahl((s or {}).get("hoehe"))
    if hoehe is not None:
        hoehe = int(hoehe)

    gebuehren, anpassung = await asyncio.gather(
        _gebuehren(tor, s, k),
        _anpassung(tor, s, k, hoehe),
    )

    return {
        "erreichbar": bool((k or {}).get("erreichbar")),
        "hoehe": hoehe,
        "gebuehren": gebuehren,
        "anpassung": anpassung,
        "bloecke": _bloecke(k),
        "halbierung": _halbierung(s, hoehe),
        # Wieviele Bloecke die Tabelle hoechstens zeigt - die Vorlage schreibt
        # die Zahl in ihre Unterzeile, statt sie doppelt zu fuehren.
        "letzte": LETZTE,
    }
