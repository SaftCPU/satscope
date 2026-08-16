"""Gebuehren: was der Schaetzer verspricht - und was die Bloecke wirklich verlangt haben.

DER BEFUND, um den es auf dieser Seite geht. Ueber sieben aufeinanderfolgende
Bloecke wurde verglichen, was vorhergesagt war und was der Block dann tatsaechlich
als Eintrittspreis verlangte:

    Wartezeit  Prognose  Wirklichkeit  Faktor
        15 s     0,315       0,315      x1,00
       189 s     0,394       0,444      x1,13
       159 s     0,367       0,452      x1,23
       441 s     0,316       0,410      x1,30
       377 s     0,309       0,457      x1,48
       896 s     0,367       0,866      x2,36

In SECHS von sieben Faellen haette die Prognose NICHT gereicht. Der Median liegt
bei x1,23, und der Aufschlag waechst mit der Wartezeit. Die Mechanik dahinter ist
keine Schwaeche der Software, sondern Arithmetik: wer genau die vorhergesagte
Grenze zahlt, ist der letzte im Block - und jede teurere Transaktion, die bis zum
Blockfund noch eintrifft, schiebt ihn wieder heraus. Je laenger es bis zum Block
dauert, desto mehr solche Transaktionen kommen zusammen.

⚠️ Diese sechs Zeilen stehen hier als BEGRUENDUNG des Entwurfs. Sie werden nicht
angezeigt: eine Messung von gestern, die wie eine Live-Zahl aussieht, waere genau
die Art Luege, die dieses Projekt vermeidet. Stattdessen rechnet die Seite
denselben Vergleich JETZT - die aktuelle Schaetzung des eigenen Knotens gegen die
Einstiegspreise der letzten BLOCKFENSTER Bloecke. Dieselbe Aussage, nur belegt.

WARUM feerate_percentiles[0] UND NICHT NUR minfeerate
minfeerate ist die absolut billigste Gebuehr im Block - und sie ist regelmaessig
verzerrt: eine Transaktion mit 1 sat/vB kommt mit hinein, wenn ein teures Kind sie
mitzieht (CPFP). Der Miner hat dann nicht 1 sat/vB akzeptiert, sondern den
gemeinsamen Satz von Eltern und Kind. Als Einstiegspreis taugt deshalb das
10. Perzentil des Blockgewichts besser: darunter lagen nur noch die
mitgeschleppten Faelle. Beide Zahlen werden erhoben, gerechnet wird mit dem
Perzentil, minfeerate steht als absoluter Boden daneben.

KOSTEN am Knoten (gemessen 15.08.2026, siehe rpc.py):
    getblockchaininfo    9 ms   einmal: Kettenspitze und Erreichbarkeit
    getmempoolinfo       7 ms   einmal: die harte Untergrenze des Knotens
    estimatesmartfee     7 ms   x6 (ein Aufruf je Ziel)
    estimaterawfee      15 ms   x6 (ein Aufruf je Ziel)
    getblockstats    19-30 ms   x12 fuer das Blockfenster  <-- der teure Teil
Zusammen rund 26 Aufrufe, seriell etwa 450 ms Knotenzeit. Das Tor laesst vier
gleichzeitig durch, die Seite steht damit nach ungefaehr 130 ms. Bewusst KEIN
Zwischenspeicher: die Seite wird von Hand aufgerufen und nicht im Sekundentakt
abgefragt - anders als das Blockband in kette.py, das genau deshalb einen hat.

VERBOTEN und hier auch nicht gebraucht: getrawmempool true haette die exakte
Verteilung der wartenden Transaktionen geliefert (und damit den perfekten
Eintrittspreis fuer den naechsten Block), kostet aber hunderte MB JSON. Die
Bloecke der letzten zwei Stunden sagen dasselbe rueckwaerts - fuer ein paar
Dutzend Kilobyte.

Dieses Modul kennt KEINE Texte. Es liefert Rohwerte, Anteile und Schluessel; die
Woerter stehen im Katalog (Vorsatz "gebuehren.").
"""
import asyncio
import math

from .rpc import RpcFehler

SAT_JE_BTC = 100000000

# Ein Block fasst 4.000.000 Gewichtseinheiten - harte Konsensgrenze, nicht die
# gern zitierte 1 MB. Daraus wird die Fuellung.
BLOCKGEWICHT = 4000000

# Ein Block fasst rund 1.000.000 virtuelle Byte Arbeit.
BLOCK_VBYTES = 1000000

# So viele Bloecke rueckwaerts. Zwoelf sind rund zwei Stunden: lang genug, dass
# ein einzelner leerer Block den Befund nicht umwirft, kurz genug, dass es noch
# "gerade jetzt" heisst. Jeder weitere Block kostet 19-30 ms auf einem Knoten,
# auf dem echtes Geld liegt.
BLOCKFENSTER = 12

# Unter so vielen erhobenen Bloecken sagen wir gar nichts ueber "die letzten
# Bloecke". Bei zwei Messwerten ist ein Median eine Behauptung, keine Beobachtung.
MINDESTBLOECKE = 3

# Bestaetigungsziele: naechster Block, zwei, drei (~halbe Stunde), sechs
# (~eine Stunde), zwoelf (~zwei Stunden), 144 (~ein Tag).
ZIELE = (1, 2, 3, 6, 12, 144)

# Typische Transaktionsgroessen in virtuellen Byte. 140 vB ist eine gewoehnliche
# Zahlung (ein Eingang, zwei Ausgaenge, P2WPKH), 220 vB eine mit zwei Eingaengen,
# 400 vB das Zusammenraeumen mehrerer kleiner Betraege.
GROESSEN = (140, 220, 400)

# Ab dieser Fuellung war der Platz im Block wirklich knapp. Nur dann sagt der
# Eintrittspreis etwas ueber Wettbewerb aus; ein halbleerer Block nimmt jeden,
# der ueber der Weiterleitungsgrenze liegt.
VOLL_AB = 95.0

# Die drei Zeithorizonte des Schaetzers in bitcoind. Reihenfolge = Vorzug: der
# kuerzeste, der ueberhaupt antwortet, ist der aussagekraeftigste.
HORIZONTE = ("short", "medium", "long")

# Nur diese Felder holen. Die Auswahl ist keine Kosmetik - bitcoind rechnet nur
# aus, wonach gefragt wird.
#
# ⚠️ Ein Feldfilter haengt an Feldnamen: kennt ein Knoten einen davon nicht,
# scheitert der GANZE Aufruf, und damit faellt hier der wichtigste Abschnitt der
# Seite aus. spiel.py fasst deshalb im Fehlerfall ungefiltert nach. Hier
# absichtlich NICHT: alle sechs Namen gibt es, seit es getblockstats gibt
# (Core 0.17, 2018), sie koennen also nicht fehlen. Ein Rueckfall waere zwoelf
# zusaetzliche Aufrufe fuer einen Fall, den es nicht gibt - und bei einem
# haengenden Knoten die doppelte Wartezeit.
FELDER = ["height", "time", "minfeerate", "feerate_percentiles",
          "total_weight", "txs"]


# --------------------------------------------------------------- Werkzeug
async def _sicher(tor, methode, *argumente):
    """Ruft auf und liefert None statt zu werfen - wie knoten._sicher.

    Absichtlich eine eigene Kopie statt eines Imports: der Strich statt einer
    erfundenen Zahl ist die wichtigste Regel dieses Projekts, und sie soll in
    jedem Modul sichtbar dastehen, nicht hinter einem fremden privaten Namen
    verschwinden.
    """
    try:
        return await tor.ruf(methode, *argumente)
    except (RpcFehler, OSError, asyncio.TimeoutError):
        return None


def _zahl(wert):
    """Nur echte, endliche Zahlen durchlassen. bool ist in Python eine Zahl -
    hier nicht, sonst wuerde aus einem True eine 1 sat/vB."""
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        return None
    if isinstance(wert, float) and (math.isnan(wert) or math.isinf(wert)):
        return None
    return wert


def _sat_pro_vb(btc_je_kvb):
    """bitcoind rechnet Gebuehren in BTC je kvB, Menschen in sat/vB.

    100.000.000 sat je BTC durch 1.000 vB je kvB macht mal 100.000. Die Rundung
    auf fuenf Stellen faengt nur den Fliesskomma-Schmutz ab (0.00001 * 1e5 ergibt
    sonst 1.0000000000000002) - sie ist keine Anzeigerundung.

    Ein fehlender Wert kommt bei bitcoind als -1 zurueck, nicht als null: dann
    hatte der Schaetzer zu wenige Daten. Das ist kein Nullpreis, sondern keine
    Antwort - und keine Antwort ist ein Strich.
    """
    w = _zahl(btc_je_kvb)
    if w is None or w <= 0:
        return None
    return round(w * 100000.0, 5)


def _eimer_sat_vb(sat_je_kvb):
    """Die Eimergrenzen von estimaterawfee stehen in sat je kvB, nicht in BTC.

    ⚠️ Zwei verschiedene Einheiten in EINER RPC-Antwort: "feerate" ist BTC/kvB,
    "startrange"/"endrange" sind sat/kvB. Wer das verwechselt, zeigt Eimer, die
    um den Faktor 100.000.000 danebenliegen - und es faellt nicht auf, weil
    beides plausible Zahlen ergibt.

    Ohne passenden Eimer setzt bitcoind -1 oder einen Unendlich-Ersatzwert
    (1e99); beides wird hier zum Strich.
    """
    w = _zahl(sat_je_kvb)
    if w is None or w <= 0 or w >= 1e12:
        return None
    return round(w / 1000.0, 5)


def _median(werte):
    werte = sorted(w for w in werte if w is not None)
    if not werte:
        return None
    mitte = len(werte) // 2
    if len(werte) % 2:
        return werte[mitte]
    return (werte[mitte - 1] + werte[mitte]) / 2.0


def _anteil(teil, ganz):
    """Prozent - oder None, wenn eine Seite fehlt. Nie durch Null."""
    if teil is None or not ganz:
        return None
    return teil / ganz * 100.0


def _balkenanteil(wert, skala):
    """Balkenlaenge in Prozent, gedeckelt und mit sichtbarem Mindestrest.

    Zwei Prozent Mindestlaenge, damit ein sehr kleiner Wert nicht zu einem
    unsichtbaren Strich wird und wie ein fehlender Wert aussieht.
    """
    if wert is None or not skala:
        return None
    return max(2.0, min(100.0, wert / skala * 100.0))


# --------------------------------------------------------------- Schaetzer
def _horizonte(roh):
    """Die rohen Eimer von estimaterawfee, je Zeithorizont.

    Das ist die Sicht, die sonst niemand zeigt. bitcoind fuehrt drei Historien
    nebeneinander (short/medium/long) mit unterschiedlicher Verfallsrate; jede
    sortiert Transaktionen in Gebuehreneimer und zaehlt mit, wie viele davon
    innerhalb des Ziels bestaetigt wurden. estimatesmartfee ist nur die
    geglaettete Auswahl daraus.

    Die interessanteste Zahl ist die Erfolgsquote des gewaehlten Eimers: sie
    liegt bauartbedingt bei 85-95 %, denn genau darauf ist der Schaetzer geeicht.
    Anders gesagt - jede zehnte bis zwanzigste Transaktion zu diesem Satz schafft
    es planmaessig NICHT ins Ziel. Das ist keine Panne, das ist die Definition.
    """
    raus = []
    for name in HORIZONTE:
        h = (roh or {}).get(name)
        if not isinstance(h, dict):
            continue
        p = h.get("pass") if isinstance(h.get("pass"), dict) else {}
        drin = _zahl(p.get("withintarget"))
        bestaetigt = _zahl(p.get("totalconfirmed"))
        wartend = _zahl(p.get("inmempool"))
        gefallen = _zahl(p.get("leftmempool"))

        # Nenner wie in bitcoinds eigener Rechnung: alles, was der Eimer
        # beobachtet hat - bestaetigt, noch wartend und aus dem Mempool
        # gefallen. Nur die im Ziel Bestaetigten zaehlen als Erfolg.
        teile = [x for x in (bestaetigt, wartend, gefallen) if x is not None]
        gesamt = sum(teile) if teile else None
        # Spaeter bestaetigt = bestaetigt, aber eben nicht mehr im Ziel. Der
        # Deckel bei 0 faengt Rundungsreste der gleitenden Mittel ab.
        spaeter = (max(0.0, bestaetigt - drin)
                   if (bestaetigt is not None and drin is not None) else None)

        # ⚠️ Eigener Nenner fuer den gestapelten Balken: die Summe genau der
        # vier gezeigten Segmente. bitcoinds Zaehler sind gleitende Mittel mit
        # Verfall und auf zwei Stellen gerundet; withintarget kann dadurch um
        # Haaresbreite ueber totalconfirmed liegen. Mit dem Nenner oben summierten
        # sich die Segmente dann auf ueber 100 % und der Balken liefe aus seinem
        # Kasten heraus. Die Erfolgsquote daneben bleibt bewusst auf bitcoinds
        # eigenem Nenner - sie soll dessen Zahl sein, nicht unsere.
        stapel = sum(x for x in (drin, spaeter, wartend, gefallen)
                     if x is not None) or None

        raus.append({
            "name": name,
            "sat_vb": _sat_pro_vb(h.get("feerate")),
            # Verfallsrate je Block: 0,962 heisst, nach einem Block zaehlt eine
            # alte Beobachtung nur noch zu 96,2 %. Der kurze Horizont vergisst
            # schnell, der lange fast nie.
            "verfall": _zahl(h.get("decay")),
            # Wie viele Bloecke zu einer Stufe zusammengefasst werden.
            "stufe": _zahl(h.get("scale")),
            "von_sat_vb": _eimer_sat_vb(p.get("startrange")),
            "bis_sat_vb": _eimer_sat_vb(p.get("endrange")),
            "im_ziel": drin,
            "spaeter": spaeter,
            "wartend": wartend,
            "gefallen": gefallen,
            "beobachtet": gesamt,
            "erfolg_p": _anteil(drin, gesamt),
            # Anteile fuer den gestapelten Balken. Jeder Wert einzeln - fehlt
            # einer, fehlt genau sein Segment.
            "im_ziel_p": _anteil(drin, stapel),
            "spaeter_p": _anteil(spaeter, stapel),
            "wartend_p": _anteil(wartend, stapel),
            "gefallen_p": _anteil(gefallen, stapel),
        })
    return raus


async def _ein_ziel(tor, ziel):
    """Beide Schaetzer fuer EIN Bestaetigungsziel, jeder einzeln abgesichert.

    Faellt estimaterawfee aus, steht trotzdem die geglaettete Schaetzung da -
    und umgekehrt. Der Vergleich der beiden entfaellt dann, nicht die Zeile.
    """
    glatt, roh = await asyncio.gather(
        _sicher(tor, "estimatesmartfee", ziel),
        _sicher(tor, "estimaterawfee", ziel))

    # Bei zu duenner Datenlage antwortet bitcoind mit "errors" statt "feerate".
    # Dann fehlt genau diese Zahl - geraten wird nichts.
    glatt_wert = _sat_pro_vb((glatt or {}).get("feerate"))
    # bitcoind darf ein ANDERES Ziel beantworten als das gefragte, wenn es fuer
    # das gefragte keine Datenlage hat. Das steht in "blocks" und ist eine
    # ehrliche Warnung - wir reichen sie durch, statt sie zu verschweigen.
    geantwortet = _zahl((glatt or {}).get("blocks"))

    horizonte = _horizonte(roh)
    # Der kuerzeste Horizont, der ueberhaupt antwortet. Er ist der schaerfste:
    # er vergisst am schnellsten und beschreibt damit die Gegenwart.
    bester = next((h for h in horizonte if h["sat_vb"] is not None), None)
    roh_wert = bester["sat_vb"] if bester else None

    return {
        "ziel": ziel,
        "glatt": glatt_wert,
        "geantwortet": int(geantwortet) if geantwortet is not None else None,
        "anderes_ziel": (geantwortet is not None
                         and int(geantwortet) != ziel),
        "roh": roh_wert,
        "roh_horizont": bester["name"] if bester else None,
        "horizonte": horizonte,
        # Wie weit die geglaettete Empfehlung ueber der Rohmessung liegt. Ein
        # positiver Wert ist der Sicherheitsabstand, den estimatesmartfee haelt;
        # ein negativer heisst, dass die Glaettung die Rohmessung unterbietet.
        "abweichung_p": ((glatt_wert / roh_wert - 1) * 100.0
                         if (glatt_wert and roh_wert) else None),
    }


async def _schaetzungen(tor):
    """Beide Schaetzer fuer alle Ziele. Zwoelf Aufrufe, nebenlaeufig."""
    return list(await asyncio.gather(*[_ein_ziel(tor, z) for z in ZIELE]))


# --------------------------------------------------------------- Wirklichkeit
def _eintritt(stats, hoehe=None):
    """Ein Block als Eintrittspreis. Der Kern der ganzen Seite.

    `hoehe` wird durchgereicht, damit ein Block auch dann seinen Platz und seine
    Nummer behaelt, wenn getblockstats fuer ihn ausgefallen ist. Ihn einfach
    wegzulassen waere die schlechtere Loesung: das Saeulenband wuerde stauchen,
    und die Luecke saehe aus wie ein Fenster, das eben nur zehn Bloecke lang ist.

    Liefert IMMER alle Schluessel. Ein in Jinja fehlender Schluessel wird zu
    Undefined, und `Undefined is not none` ist wahr - eine Abfrage in der Vorlage
    liefe also durch und druckte eine leere Saeule.
    """
    b = {"hoehe": hoehe, "zeit": None, "min_sat_vb": None, "p10_sat_vb": None,
         "median_sat_vb": None, "eintritt": None, "fuellung_p": None,
         "voll": False, "txs": None, "anteil_p": None, "ueber_prognose": False}
    if not isinstance(stats, dict):
        return b

    # Kein "or": Hoehe 0 ist der Genesis-Block und eine gueltige Antwort, waere
    # aber falsch. Nur eine wirklich fehlende Hoehe faellt auf das Argument zurueck.
    gemeldet = _zahl(stats.get("height"))
    b["hoehe"] = gemeldet if gemeldet is not None else hoehe
    b["zeit"] = _zahl(stats.get("time"))
    b["txs"] = _zahl(stats.get("txs"))
    b["min_sat_vb"] = _zahl(stats.get("minfeerate"))

    p = stats.get("feerate_percentiles")
    if isinstance(p, list) and len(p) == 5 and all(
            _zahl(x) is not None for x in p):
        b["p10_sat_vb"] = p[0]
        b["median_sat_vb"] = p[2]

    gewicht = _zahl(stats.get("total_weight"))
    b["fuellung_p"] = _anteil(gewicht, BLOCKGEWICHT)
    b["voll"] = bool(b["fuellung_p"] is not None and b["fuellung_p"] >= VOLL_AB)

    # Das 10. Perzentil ist der belastbare Eintrittspreis; minfeerate faellt nur
    # ein, wenn das Perzentil fehlt (aeltere Knoten liefern es nicht immer).
    b["eintritt"] = b["p10_sat_vb"] if b["p10_sat_vb"] is not None else b["min_sat_vb"]
    return b


async def _blockreihe(tor, spitze):
    """getblockstats fuer die letzten BLOCKFENSTER Bloecke, aeltester zuerst.

    Ueber die HOEHE und nicht ueber den Hash: das spart zwoelf getblockhash und
    ist hier ungefaehrlich. Faellt zwischen zwei Aufrufen eine Reorganisation,
    bekommen wir den Block der neuen besten Kette - fuer eine Gebuehrenstatistik
    ist genau das der richtige. (Auf der Blockseite ist es umgekehrt, dort geht
    es um EINEN bestimmten Block, und der darf nicht getauscht werden.)

    Ausfaelle einzelner Bloecke sind eingeplant: der Block BEHAELT seinen Platz
    im Band und zeigt dort nichts. Er zaehlt damit zum Fenster, aber nicht zu den
    gemessenen Werten - siehe _wirklichkeit(). Der Befund wird davon schwaecher,
    nie falsch.
    """
    if spitze is None:
        return []
    hoehen = [h for h in range(int(spitze) - BLOCKFENSTER + 1, int(spitze) + 1)
              if h >= 0]
    if not hoehen:
        return []
    roh = await asyncio.gather(*[
        _sicher(tor, "getblockstats", h, FELDER) for h in hoehen])
    return [_eintritt(s, hoehe=h) for h, s in zip(hoehen, roh)]


def _wirklichkeit(bloecke):
    """Was die erhobenen Bloecke zusammengenommen verlangt haben.

    Drei Zahlen, die der Empfehlung unten als Boden dienen: der guenstigste, der
    mittlere und der teuerste Eintrittspreis des Fensters. Unter MINDESTBLOECKE
    Messwerten bleibt alles leer - lieber keine Einordnung als eine, die auf zwei
    Bloecken beruht.
    """
    w = {"n": len(bloecke), "gemessen": 0, "genug": False,
         "p10_min": None, "p10_median": None, "p10_max": None,
         "min_median": None, "median_median": None,
         "teuerster": None, "billigster": None,
         "voll_anzahl": None, "voll_p": None, "stunden": None}

    eintritte = [b["eintritt"] for b in bloecke if b["eintritt"] is not None]
    # ⚠️ "gemessen" ist NICHT dasselbe wie "n": ein Block ohne Statistik zaehlt
    # zum Fenster, aber nicht zum Median. Die Seite nennt deshalb ueberall dort,
    # wo sie ueber Preise spricht, die gemessene Zahl - sonst behauptet sie eine
    # Grundlage, die sie nicht hat.
    w["gemessen"] = len(eintritte)
    if len(eintritte) < MINDESTBLOECKE:
        return w

    w["genug"] = True
    w["p10_min"] = min(eintritte)
    w["p10_max"] = max(eintritte)
    w["p10_median"] = _median(eintritte)
    w["min_median"] = _median([b["min_sat_vb"] for b in bloecke])
    w["median_median"] = _median([b["median_sat_vb"] for b in bloecke])

    # Welcher Block der teuerste bzw. billigste war - die Zahl bekommt damit
    # einen Ort, an dem man sie nachsehen kann.
    mit_hoehe = [(b["eintritt"], b["hoehe"]) for b in bloecke
                 if b["eintritt"] is not None and b["hoehe"] is not None]
    if mit_hoehe:
        w["teuerster"] = max(mit_hoehe)[1]
        w["billigster"] = min(mit_hoehe)[1]

    voll = [b for b in bloecke if b["voll"]]
    w["voll_anzahl"] = len(voll)
    w["voll_p"] = _anteil(len(voll), len(bloecke))

    zeiten = [b["zeit"] for b in bloecke if b["zeit"] is not None]
    if len(zeiten) >= 2:
        w["stunden"] = max(0.0, (max(zeiten) - min(zeiten)) / 3600.0)
    return w


def _vergleich(bloecke, prognose):
    """Die entscheidende Rechnung: wie oft haette die Prognose nicht gereicht?

    Verglichen wird die AKTUELLE Schaetzung fuer den naechsten Block gegen die
    Eintrittspreise der letzten Bloecke. Das ist keine Rueckrechnung dessen, was
    damals vorhergesagt war - dazu muesste der Knoten seine alten Schaetzungen
    aufbewahren, und das tut er nicht. Es ist die ehrlichere Frage: wuerde die
    Zahl, die dir dein Knoten JETZT nennt, in die Bloecke der letzten zwei
    Stunden hineingereicht haben?

    Der Faktor je Block (Wirklichkeit geteilt durch Prognose) ist dieselbe
    Groesse, die in der Messreihe oben im Modulkopf steht.
    """
    v = {"n": 0, "darueber": None, "darueber_p": None,
         "faktor_median": None, "faktor_max": None, "prognose": prognose}
    eintritte = [b["eintritt"] for b in bloecke if b["eintritt"] is not None]
    v["n"] = len(eintritte)
    if not prognose or len(eintritte) < MINDESTBLOECKE:
        return v

    v["darueber"] = sum(1 for e in eintritte if e > prognose)
    v["darueber_p"] = _anteil(v["darueber"], len(eintritte))
    faktoren = [e / prognose for e in eintritte]
    v["faktor_median"] = _median(faktoren)
    v["faktor_max"] = max(faktoren)
    return v


# --------------------------------------------------------------- Empfehlung
def _boden_art(ziel):
    """Welcher gemessene Boden fuer welches Ziel gilt.

    Die Staffel folgt der Mechanik aus dem Modulkopf, nicht dem Geschmack:

    * 1-2 Bloecke - der TEUERSTE Eintritt des Fensters. Wer in den naechsten
      Block will, muss auch den Fall schlagen, in dem der naechste Block einer
      der teuren ist. Genau hier ist die uebliche Schaetzung zu optimistisch.
    * 3-12 Bloecke - der MITTLERE Eintritt. Ueber mehrere Bloecke mittelt sich
      der Ausreisser heraus; wer eine Stunde Zeit hat, muss den schlimmsten
      Block nicht bezahlen.
    * darueber - der GUENSTIGSTE Eintritt. Tiefer als das kam im ganzen Fenster
      in keinen einzigen Block etwas hinein, und tiefer empfehlen wir deshalb
      auch fuer einen Tag Geduld nicht.
    """
    if ziel <= 2:
        return "max"
    if ziel <= 12:
        return "median"
    return "min"


def _empfehlungen(schaetzungen, w, mempool_min):
    """Prognose und Wirklichkeit zu einer Empfehlung verbinden.

    Die Empfehlung ist das MAXIMUM aus drei gemessenen Groessen und keine
    Hochrechnung: der Schaetzung des Knotens, dem Boden aus den letzten Bloecken
    und der Untergrenze des eigenen Mempools. Es wird nichts multipliziert und
    nichts aufgeschlagen - jeder Balken auf der Seite ist eine Zahl, die
    irgendwo wirklich gemessen wurde.

    Faellt eine der drei Quellen aus, entscheidet die uebrigen. Fallen alle drei
    aus, gibt es keine Empfehlung und dort steht ein Strich.
    """
    raus = []
    for s in schaetzungen:
        art = _boden_art(s["ziel"])
        boden = w.get("p10_" + art) if w.get("genug") else None

        kandidaten = [("prognose", s["glatt"]), ("bloecke", boden),
                      ("mempool", mempool_min)]
        gueltig = [(name, wert) for name, wert in kandidaten if wert is not None]
        if gueltig:
            gebunden, empfehlung = max(gueltig, key=lambda paar: paar[1])
        else:
            gebunden, empfehlung = None, None

        # Der Aufschlag ist keine Zutat, sondern ein Ergebnis: um so viel liegt
        # die Empfehlung ueber dem, was der Knoten allein gesagt haette.
        aufschlag_p = ((empfehlung / s["glatt"] - 1) * 100.0
                       if (empfehlung and s["glatt"]) else None)

        raus.append({
            "ziel": s["ziel"],
            "prognose": s["glatt"],
            "boden": boden,
            "boden_art": art,
            "mempool_min": mempool_min,
            "empfehlung": empfehlung,
            "gebunden": gebunden,
            "aufschlag_p": aufschlag_p if (aufschlag_p or 0) >= 0.5 else None,
            # Was diese Empfehlung eine Transaktion kostet. Ganze Satoshi -
            # Bruchteile davon gibt es nicht.
            "kosten": {vb: (int(round(empfehlung * vb)) if empfehlung else None)
                       for vb in GROESSEN},
            "kosten_prognose": {vb: (int(round(s["glatt"] * vb))
                                     if s["glatt"] else None)
                                for vb in GROESSEN},
            # Balkenanteile setzt _skalieren() nach, wenn alle Zeilen da sind.
            "prognose_p": None, "empfehlung_p": None, "boden_p": None,
        })
    return _skalieren(raus)


def _skalieren(empfehlungen):
    """Balkenlaengen ueber ALLE Zeilen an einem Massstab.

    Der Massstab ist der groesste vorkommende Wert - nur so sind die Zeilen
    untereinander vergleichbar, und genau das ist die Aussage: die Empfehlung
    fuer den naechsten Block ist ein Vielfaches der fuer morgen.
    """
    werte = [e[n] for e in empfehlungen for n in ("prognose", "empfehlung", "boden")
             if e[n] is not None]
    skala = max(werte) if werte else None
    for e in empfehlungen:
        e["prognose_p"] = _balkenanteil(e["prognose"], skala)
        e["empfehlung_p"] = _balkenanteil(e["empfehlung"], skala)
        e["boden_p"] = _balkenanteil(e["boden"], skala)
    return empfehlungen


# --------------------------------------------------------------- Hauptweg
def _leer(erreichbar=False):
    """Immer derselbe Satz Schluessel - auch wenn nichts erhoben werden konnte."""
    return {
        "erreichbar": erreichbar,
        "spitze": None,
        "fenster": BLOCKFENSTER,
        "groessen": list(GROESSEN),
        "bloecke": [],
        "w": _wirklichkeit([]),
        "schaetzungen": [],
        "empfehlungen": [],
        "vergleich": _vergleich([], None),
        "leitziel": None,
        "leithorizonte": [],
        "mempool_min_sat_vb": None,
        "mempool_bloecke": None,
        "chart_linie_p": None,
    }


async def seite(tor):
    """Alles, was gebuehren.html braucht. Ein Aufruf, kein Wissen ueber den Aufbau.

    Zwei Runden: erst die Kettenspitze (ohne sie wissen wir nicht, welche Bloecke
    zu holen sind), dann alles Uebrige nebenlaeufig.
    """
    kette = await _sicher(tor, "getblockchaininfo")
    if kette is None:
        # Der Knoten schweigt. Kein erfundener Ersatz, keine halbe Seite.
        return _leer(erreichbar=False)

    spitze = _zahl(kette.get("blocks"))
    spitze = int(spitze) if spitze is not None else None

    schaetzungen, bloecke, mempool = await asyncio.gather(
        _schaetzungen(tor),
        _blockreihe(tor, spitze),
        _sicher(tor, "getmempoolinfo"))

    # Die harte Untergrenze des eigenen Knotens: darunter nimmt er eine
    # Transaktion nicht einmal an, egal was ein Schaetzer sagt.
    mempool_min = _sat_pro_vb((mempool or {}).get("mempoolminfee"))
    mempool_bytes = _zahl((mempool or {}).get("bytes"))

    w = _wirklichkeit(bloecke)
    empfehlungen = _empfehlungen(schaetzungen, w, mempool_min)

    prognose1 = next((s["glatt"] for s in schaetzungen if s["ziel"] == 1), None)
    vergleich = _vergleich(bloecke, prognose1)

    # Massstab der Saeulen: der groesste Eintrittspreis oder die Prognoselinie,
    # je nachdem, was hoeher liegt. Sonst laege die Linie ausserhalb des Bildes,
    # und genau ihr Verhaeltnis zu den Saeulen ist die Aussage.
    eintritte = [b["eintritt"] for b in bloecke if b["eintritt"] is not None]
    skala = max(eintritte + ([prognose1] if prognose1 else [])) if eintritte else None
    for b in bloecke:
        b["anteil_p"] = _balkenanteil(b["eintritt"], skala)
        b["ueber_prognose"] = bool(prognose1 and b["eintritt"] is not None
                                   and b["eintritt"] > prognose1)

    leitziel = next((e for e in empfehlungen if e["ziel"] == 1), None)
    leithorizonte = next((s["horizonte"] for s in schaetzungen if s["ziel"] == 1), [])
    # Faellt der kurze Horizont fuer Ziel 1 aus (bitcoind antwortet dort nicht
    # immer), nehmen wir die Eimer des naechstbesten Ziels - die Bauart des
    # Schaetzers zeigt sich an jedem von ihnen.
    if not leithorizonte:
        leithorizonte = next((s["horizonte"] for s in schaetzungen
                              if s["horizonte"]), [])

    d = _leer(erreichbar=True)  # gleicher Schluesselsatz, gefuellt
    d.update({
        "spitze": spitze,
        "bloecke": bloecke,
        "w": w,
        "schaetzungen": schaetzungen,
        "empfehlungen": empfehlungen,
        "vergleich": vergleich,
        "leitziel": leitziel,
        "leithorizonte": leithorizonte,
        "mempool_min_sat_vb": mempool_min,
        "mempool_bloecke": (mempool_bytes / BLOCK_VBYTES
                            if mempool_bytes is not None else None),
        "chart_linie_p": _balkenanteil(prognose1, skala),
    })
    return d


# --------------------------------------------------------------- Selbsttest
def _selbsttest():
    """Die Rechnung dieses Moduls ohne Netz pruefen:

        python3 -m satscope.gebuehren

    Bewusst hier und nicht in selbsttest.py: an dieser Datei arbeitet gerade
    jemand anders. Sobald der Umbau vorbei ist, gehoert der Aufruf dort hinein.
    Geprueft wird genau das, was ein spaeterer Umbau still kaputtmachen kann -
    Einheiten, Mediane und die Rangfolge der Empfehlung.
    """
    fehler = []

    def p(name, ist, soll):
        if ist != soll:
            fehler.append("%s: %r erwartet, %r bekommen" % (name, soll, ist))
            print("  FEHLER  %s" % name)
        else:
            print("  ok      %s" % name)

    print("Einheiten")
    # 0,00001 BTC/kvB sind genau 1 sat/vB - ohne die Rundung kaeme hier
    # 1.0000000000000002 heraus und jeder Vergleich "> 1" waere wahr.
    p("BTC/kvB -> sat/vB", _sat_pro_vb(0.00001), 1.0)
    p("keine Antwort ist keine Null", _sat_pro_vb(-1), None)
    p("Null ist keine Gebuehr", _sat_pro_vb(0), None)
    p("bool ist keine Zahl", _sat_pro_vb(True), None)
    # ⚠️ Eimergrenzen kommen in sat/kvB, nicht in BTC/kvB - dieselbe Antwort,
    # zwei Einheiten. Wer das verwechselt, liegt um 1e8 daneben.
    p("sat/kvB -> sat/vB", _eimer_sat_vb(2500), 2.5)
    p("Unendlich-Ersatzwert faellt weg", _eimer_sat_vb(1e99), None)

    print("\nMediane")
    p("ungerade", _median([3, 1, 2]), 2)
    p("gerade", _median([1, 2, 3, 4]), 2.5)
    p("leer", _median([]), None)
    p("Luecken zaehlen nicht mit", _median([1, None, 3]), 2)

    print("\nBoden je Ziel")
    p("naechster Block nimmt den teuersten", _boden_art(1), "max")
    p("zwei Bloecke auch", _boden_art(2), "max")
    p("drei Bloecke nehmen den Median", _boden_art(3), "median")
    p("zwoelf Bloecke noch", _boden_art(12), "median")
    p("darueber der guenstigste", _boden_art(13), "min")

    print("\nEintrittspreis eines Blocks")
    voll = {"height": 9, "minfeerate": 1, "total_weight": 3990000,
            "feerate_percentiles": [4, 5, 6, 7, 8], "time": 1, "txs": 2}
    b = _eintritt(voll)
    p("Perzentil schlaegt minfeerate", b["eintritt"], 4)
    p("minfeerate bleibt sichtbar", b["min_sat_vb"], 1)
    p("voller Block erkannt", b["voll"], True)
    ohne_p = dict(voll)
    del ohne_p["feerate_percentiles"]
    p("Rueckfall auf minfeerate", _eintritt(ohne_p)["eintritt"], 1)
    p("ohne Statistik bleibt die Hoehe", _eintritt(None, hoehe=7)["hoehe"], 7)
    p("ohne Statistik kein Preis", _eintritt(None, hoehe=7)["eintritt"], None)

    print("\nEmpfehlung: das Groesste aus drei gemessenen Zahlen")
    w = {"genug": True, "p10_min": 1.0, "p10_median": 4.0, "p10_max": 9.0}
    e = _empfehlungen([{"ziel": 1, "glatt": 3.0}], w, 1.0)[0]
    p("Bloecke gewinnen", (e["empfehlung"], e["gebunden"]), (9.0, "bloecke"))
    p("Aufschlag ist ein Ergebnis", round(e["aufschlag_p"]), 200)
    p("Kosten in ganzen Satoshi", e["kosten"][220], 1980)
    e = _empfehlungen([{"ziel": 144, "glatt": 3.0}], w, 1.0)[0]
    p("Prognose gewinnt bei Geduld", (e["empfehlung"], e["gebunden"]),
      (3.0, "prognose"))
    p("ohne Aufschlag kein Abzeichen", e["aufschlag_p"], None)
    e = _empfehlungen([{"ziel": 1, "glatt": None}], {"genug": False}, 2.5)[0]
    p("nur der Mempool antwortet", (e["empfehlung"], e["gebunden"]),
      (2.5, "mempool"))
    e = _empfehlungen([{"ziel": 1, "glatt": None}], {"genug": False}, None)[0]
    p("gar keine Quelle -> Strich", e["empfehlung"], None)
    p("und keine Kosten", e["kosten"][140], None)

    print("\nVergleich Prognose gegen Wirklichkeit")
    reihe = [{"eintritt": x} for x in (1.0, 2.0, 4.0, 8.0)]
    v = _vergleich(reihe, 2.0)
    p("wie viele lagen darueber", v["darueber"], 2)
    p("Median-Faktor", v["faktor_median"], 1.5)
    p("schlimmster Faktor", v["faktor_max"], 4.0)
    p("ohne Prognose kein Urteil", _vergleich(reihe, None)["darueber"], None)
    p("zu wenige Bloecke, kein Urteil",
      _vergleich([{"eintritt": 1.0}], 2.0)["darueber"], None)

    print("\nGestapelter Balken summiert sich auf 100 %")
    # ⚠️ Hier absichtlich WIDERSPRUECHLICH: withintarget > totalconfirmed, wie es
    # durch Rundung der gleitenden Mittel vorkommen kann. Die Segmente muessen
    # trotzdem in ihren Kasten passen.
    h = _horizonte({"short": {"feerate": 0.00002, "decay": 0.962, "scale": 1,
                              "pass": {"withintarget": 100.0,
                                       "totalconfirmed": 90.0,
                                       "inmempool": 5.0, "leftmempool": 5.0}}})[0]
    summe = sum(h[n] for n in ("im_ziel_p", "spaeter_p", "wartend_p", "gefallen_p"))
    p("Segmente summieren sich", round(summe, 6), 100.0)
    p("spaeter nie negativ", h["spaeter"], 0.0)

    print("\nNur billige RPC-Methoden")
    import asyncio as _a

    from .probeknoten import ProbeTor
    tor = ProbeTor()
    d = _a.run(seite(tor))
    benutzt = sorted({m for m, _ in tor.aufrufe})
    from .rpc import BILLIG
    p("keine teure Methode", sorted(set(benutzt) - set(BILLIG)), [])
    p("Aufrufzahl bleibt im Rahmen", len(tor.aufrufe) <= 30, True)
    p("Seite ist vollstaendig", sorted(d) == sorted(_leer()), True)

    print("\n%d Fehler" % len(fehler))
    for f in fehler:
        print("  " + f)
    return 1 if fehler else 0


if __name__ == "__main__":
    import sys

    sys.exit(_selbsttest())
