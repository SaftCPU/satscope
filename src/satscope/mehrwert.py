"""Der fuenfte Kasten: was ein oeffentlicher Explorer NICHT beantworten kann.

Die vier Kaesten darueber bauen mempool.space nach. Dieser hier ist der Grund,
warum es Satscope ueberhaupt gibt: er zeigt fuenf Dinge, die auf einer fremden
Maschine gar nicht zu haben sind, weil sie den EIGENEN Knoten voraussetzen.

    1 Knoten und Index einig?   Die Hoehe des Electrum-Index gegen die des
      Knotens. Haengt der Index zurueck, sind Adressabfragen still
      unvollstaendig - und beide Seiten sehen dabei je fuer sich gesund aus.
      Genau deshalb sieht man es sonst nirgends: kein Dienst vergleicht sie.
    2 Zwei Gebuehrenschaetzer nebeneinander. estimatesmartfee ist geglaettet
      und aufgerundet, estimaterawfee zeigt den Eimer, den bitcoind wirklich
      gemessen hat - samt Trefferquote und Verfallsrate.
    3 Was die letzten Bloecke WIRKLICH verlangt haben (getblockstats ->
      minfeerate) gegen die Prognose von jetzt, als Balkenpaar je Block.
    4 Konkurrierende Kettenspitzen aus getchaintips: die Zweige, die dein
      Knoten gesehen und beiseitegelegt hat.
    5 Kein Nachhausetelefonieren - als GEPRUEFTE Zusage, nicht als Behauptung.

DER BEFUND ZU (3), am eigenen Knoten gemessen. Ueber sieben aufeinanderfolgende
Bloecke verglichen: in SECHS Faellen haette die uebliche Prognose NICHT
gereicht, der Median lag bei Faktor x1,23, und der Aufschlag waechst mit der
Wartezeit. Die Mechanik dahinter ist keine Schwaeche der Software, sondern
Arithmetik: wer genau die vorhergesagte Grenze zahlt, ist der Letzte im Block -
jede teurere Transaktion, die bis zum Blockfund noch eintrifft, schiebt ihn
wieder heraus.

⚠️ Diese Messung steht hier als BEGRUENDUNG des Entwurfs und wird NICHT
angezeigt. Eine Zahl von gestern, die wie eine Live-Zahl aussieht, waere genau
die Art Luege, die dieses Projekt vermeidet. Der Kasten rechnet denselben
Vergleich JETZT, mit den Bloecken dieses Knotens - dieselbe Aussage, nur belegt.
(Dieselbe Regel wie im Kopf von gebuehren.py, dort steht die volle Messreihe.)

KOSTEN, am Knoten gemessen (15.08.2026, siehe rpc.py):
    getblockchaininfo     9 ms   einmal, fuer Spitze und Spitzenhash
    estimatesmartfee      7 ms   einmal, Ziel 1
    estimaterawfee       15 ms   einmal, Ziel 1
    getblockstats     19-30 ms   x8 fuer das Blockfenster  <-- der teure Teil
    getchaintips        114 ms   der teuerste Einzelaufruf, deshalb gepuffert
    elektrum.index_hoehe  6 ms   kein RPC, eine Zeile ueber TCP
Der Kasten sitzt auf der STARTSEITE, und F5 ist die haeufigste Nutzergeste.
Deshalb liegen die beiden teuren Quellen hinter einem Puffer: das Blockfenster
am Spitzenhash (es aendert sich nur mit einem neuen Block), die Kettenspitzen
an einer 30-s-Frist. Frisch bleibt, was frisch sein MUSS - der Abgleich mit dem
Index ist der Kern dieses Kastens und wird bei jedem Aufruf neu gemessen.

Verboten und hier auch nicht gebraucht: getblock Stufe 2/3, getrawmempool true,
getblocktemplate, gettxoutsetinfo, scanblocks. Jede Methode oben steht in
rpc.BILLIG; das Web-Tor kennt die teuren gar nicht.

Dieses Modul kennt KEINE Texte. Es liefert Rohwerte, Anteile und Schluessel;
die Woerter stehen im Katalog (Vorsatz "mehrwert."). Es liefert IMMER denselben
Satz Schluessel - ein fehlender wird in Jinja zu Undefined, und
`Undefined is not none` ist wahr: eine Abfrage in der Vorlage liefe durch und
druckte eine leere Zeile.

Selbstpruefung ohne Knoten und ohne Netz:  python3 -m satscope.mehrwert
"""
import asyncio
import ipaddress
import math
import os
import re
import time

from . import elektrum
from .rpc import RpcFehler

# Bestaetigungsziel fuer beide Schaetzer: der naechste Block. Nur dort stehen
# sie wirklich nebeneinander - fuer 144 Bloecke hat der rohe Schaetzer oft gar
# keinen Eimer, und ein Vergleich mit einem Strich ist keiner.
ZIEL = 1

# So viele Bloecke rueckwaerts. Acht sind rund 80 Minuten: lang genug, dass ein
# einzelner leerer Block den Befund nicht umwirft, kurz genug, dass es noch
# "gerade jetzt" heisst. Jeder weitere Block kostet 19-30 ms auf einem Knoten,
# auf dem echtes Geld liegt. (Der Auftrag nennt 6 bis 12 - acht liegt mittig.)
BLOCKFENSTER = 8

# Unter so vielen gemessenen Bloecken sagen wir nichts ueber "die letzten
# Bloecke". Bei zwei Messwerten ist ein Median eine Behauptung, keine Messung.
MINDESTBLOECKE = 3

# Nur diese Felder holen - bitcoind rechnet nur aus, wonach gefragt wird.
# Alle drei gibt es, seit es getblockstats gibt (Core 0.17), ein Rueckfall auf
# den ungefilterten Aufruf waere also acht Aufrufe fuer einen Fall, den es nicht
# gibt. (spiel.py faellt zurueck, weil es dort exotischere Felder sind.)
FELDER = ["height", "time", "minfeerate"]

# Kettenspitzen aendern sich hoechstens im Blocktakt; 30 s Puffer verlieren
# nichts und halten 114 ms von jedem zweiten Seitenaufruf fern.
SPITZEN_TTL = 30.0

# So viele Zweige werden aufgelistet. Ein lange laufender Knoten sammelt
# Dutzende reiner Kopfzeilen-Spitzen an - ungefiltert waere das eine Bleiwueste
# in einer Kachel, die vier Zeilen hoch ist.
SPITZEN_ZEILEN = 4

# Zustaende mit Erklaertext im Katalog. Kommt in einer kuenftigen Core-Fassung
# einer dazu, zeigt die Vorlage den Rohwert - besser als "!tips.state.xyz!".
SPITZEN_ZUSTAENDE = frozenset({
    "active", "valid-fork", "valid-headers", "headers-only", "invalid"})

# War eine Erhebung unvollstaendig (ein getblockstats fiel aus), darf sie sich
# nicht bis zum naechsten Block einbrennen.
NACHFASSEN = 60.0


# --------------------------------------------------------------- Werkzeug
async def _sicher(tor, methode, *argumente):
    """Ruft auf und liefert None statt zu werfen.

    Absichtlich eine eigene Kopie und kein Import aus einem fremden Modul: der
    Strich statt einer erfundenen Zahl ist die wichtigste Regel dieses Projekts,
    und sie soll in jedem Modul sichtbar dastehen statt hinter einem privaten
    Namen zu verschwinden, den beim naechsten Umbau jemand verschiebt.
    """
    try:
        return await tor.ruf(methode, *argumente)
    except (RpcFehler, OSError, asyncio.TimeoutError):
        return None


def _zahl(wert):
    """Nur echte, endliche Zahlen durchlassen.

    bool ist in Python eine Zahl - hier nicht: aus einem True wuerde sonst eine
    Gebuehr von 1 sat/vB, und das saehe man der Anzeige nicht mehr an.
    """
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        return None
    if isinstance(wert, float) and (math.isnan(wert) or math.isinf(wert)):
        return None
    return wert


def _sat_pro_vb(btc_je_kvb):
    """bitcoind rechnet Gebuehren in BTC je kvB, Menschen in sat/vB.

    100.000.000 sat je BTC durch 1.000 vB je kvB macht mal 100.000. Die Rundung
    auf fuenf Stellen faengt nur den Fliesskomma-Schmutz ab (0.00001 * 1e5 ist
    sonst 1.0000000000000002), sie ist keine Anzeigerundung.

    Hat der Schaetzer zu wenige Daten, antwortet bitcoind mit -1 und nicht mit
    null. Das ist kein Nullpreis, sondern keine Antwort - und keine Antwort ist
    ein Strich.
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
    return teil * 100.0 / ganz


def _balken(wert, skala):
    """Balkenlaenge in Prozent, gedeckelt, mit sichtbarem Mindestrest.

    Zwei Prozent Mindestlaenge: ein sehr kleiner Wert soll nicht zu einem
    unsichtbaren Strich werden und dadurch wie ein FEHLENDER Wert aussehen -
    der ist im ganzen Projekt ein "–" und nichts anderes.
    """
    if wert is None or not skala or skala <= 0:
        return None
    return max(2.0, min(100.0, wert * 100.0 / skala))


# ------------------------------------------------- 1 Knoten und Index einig?
def _index_stand(knoten, index, eingerichtet=True, ms=None):
    """Der Abgleich als reine Rechnung - ohne Netz, damit pruefbar.

    Die Stufen sind bewusst nicht "gleich oder kaputt":

    * 0 Bloecke  - einig, alles gut.
    * 1 Block    - der Index haengt einen Block zurueck. Das ist unmittelbar
                   nach einem Block der NORMALFALL und keine Stoerung; er
                   braucht Sekunden, um den neuen Block einzusortieren.
    * >= 2       - jetzt ist es ein Befund: Adressabfragen liefern still eine
                   unvollstaendige Historie, und niemand sagt es einem.
    * negativ    - der Index meldet eine hoehere Spitze als der Knoten. Er
                   folgt eigentlich dem Knoten; steht das dauerhaft da, zeigen
                   beide nicht auf dieselbe Maschine.
    """
    d = {"knoten": knoten, "index": index, "abstand": None,
         "zustand": None, "klasse": "neutral", "satz": None, "ms": ms,
         "gemessen": False}
    if not eingerichtet:
        d["zustand"] = "kein_server"
    elif index is None:
        d["zustand"] = "stumm"
        d["klasse"] = "schlecht"
    elif knoten is None:
        d["zustand"] = "unbekannt"
    else:
        abstand = int(knoten) - int(index)
        d["abstand"] = abstand
        d["gemessen"] = True
        if abstand == 0:
            d["zustand"], d["klasse"] = "gleich", "gut"
        elif abstand < 0:
            d["zustand"], d["klasse"] = "voraus", "warn"
        elif abstand == 1:
            d["zustand"], d["klasse"] = "kurz", "warn"
        else:
            d["zustand"], d["klasse"] = "zurueck", "schlecht"
    d["satz"] = "mehrwert.index.zustand." + d["zustand"]
    return d


async def _index(knoten, ziel=None, zeitlimit=4.0):
    """Indexhoehe holen und dabei die Zeit stoppen.

    `ziel` ist (Host, Port) und dient nur dem Selbsttest: mit ("", "") laeuft
    dieser Zweig ohne einen einzigen Netzzugriff. Im Betrieb steht None dort,
    dann entscheidet die Umgebung (ELECTRUM_HOST/PORT) wie ueberall sonst.

    Auch wenn der KNOTEN schweigt, wird der Index gefragt: die Zahl ist dann
    zwar nicht vergleichbar, aber sie ist gemessen - und eine gemessene Zahl
    wegzuwerfen, weil eine andere fehlt, waere die falsche Sparsamkeit.
    """
    host, port = elektrum.ziel() if ziel is None else ziel
    if not host or not port:
        return _index_stand(knoten, None, eingerichtet=False)
    start = time.monotonic()
    hoehe = await elektrum.index_hoehe(host, port, zeitlimit)
    return _index_stand(knoten, hoehe,
                        ms=(time.monotonic() - start) * 1000.0)


# ---------------------------------------------- 2 Zwei Schaetzer nebeneinander
def _schaetzer(glatt, roh):
    """estimatesmartfee gegen den rohen Eimer aus estimaterawfee.

    Was der Unterschied bedeutet: bitcoind fuehrt drei Historien nebeneinander
    (short/medium/long) mit unterschiedlicher Verfallsrate. Jede sortiert
    Transaktionen in Gebuehreneimer und zaehlt mit, wie viele davon innerhalb
    des Ziels bestaetigt wurden. estimatesmartfee ist die geglaettete, nach
    oben gerundete Auswahl daraus; estimaterawfee zeigt den Eimer selbst.

    Die Trefferquote liegt bauartbedingt bei 85-95 % - genau darauf ist der
    Schaetzer geeicht. Anders gesagt: jede zehnte bis zwanzigste Transaktion zu
    diesem Satz schafft es planmaessig NICHT ins Ziel. Das ist keine Panne, das
    ist die Definition, und sie steht sonst nirgends.

    Nimmt die ROHEN RPC-Antworten, damit die ganze Rechnung ohne Knoten
    pruefbar bleibt. Liefert IMMER denselben Schluesselsatz.
    """
    d = dict.fromkeys((
        "glatt", "roh", "geantwortet", "von_sat_vb", "bis_sat_vb", "im_ziel",
        "bestaetigt", "wartend", "gefallen", "verfall", "stufe", "erfolg_p",
        "faktor", "abweichung_p", "glatt_p", "roh_p"))
    d["anderes_ziel"] = False
    d["beide"] = False

    d["glatt"] = _sat_pro_vb((glatt or {}).get("feerate"))
    # bitcoind darf ein ANDERES Ziel beantworten als das gefragte, wenn ihm
    # fuer das gefragte die Datenlage fehlt. Das steht in "blocks" und ist eine
    # ehrliche Warnung - wir reichen sie durch, statt sie zu verschweigen.
    geantwortet = _zahl((glatt or {}).get("blocks"))
    d["geantwortet"] = int(geantwortet) if geantwortet is not None else None
    d["anderes_ziel"] = bool(d["geantwortet"] is not None
                             and d["geantwortet"] != ZIEL)

    kurz = (roh or {}).get("short")
    if isinstance(kurz, dict):
        p = kurz.get("pass") if isinstance(kurz.get("pass"), dict) else {}
        d["roh"] = _sat_pro_vb(kurz.get("feerate"))
        # Verfall 0,962 heisst: nach einem Block zaehlt eine alte Beobachtung
        # nur noch zu 96,2 %. Der kurze Horizont vergisst schnell - deshalb ist
        # er der schaerfste Blick auf die Gegenwart.
        d["verfall"] = _zahl(kurz.get("decay"))
        d["stufe"] = _zahl(kurz.get("scale"))
        d["von_sat_vb"] = _eimer_sat_vb(p.get("startrange"))
        d["bis_sat_vb"] = _eimer_sat_vb(p.get("endrange"))
        d["im_ziel"] = _zahl(p.get("withintarget"))
        d["bestaetigt"] = _zahl(p.get("totalconfirmed"))
        d["wartend"] = _zahl(p.get("inmempool"))
        d["gefallen"] = _zahl(p.get("leftmempool"))
        # Nenner wie in bitcoinds eigener Rechnung: alles, was der Eimer
        # beobachtet hat - bestaetigt, noch wartend und aus dem Mempool
        # gefallen. Nur die im Ziel Bestaetigten zaehlen als Erfolg.
        teile = [x for x in (d["bestaetigt"], d["wartend"], d["gefallen"])
                 if x is not None]
        d["erfolg_p"] = _anteil(d["im_ziel"], sum(teile) if teile else None)

    if d["glatt"] and d["roh"]:
        d["beide"] = True
        d["faktor"] = d["glatt"] / d["roh"]
        d["abweichung_p"] = (d["faktor"] - 1.0) * 100.0
    # Beide Balken an EINEM Massstab - sonst waeren zwei gleich lange Balken
    # unter zwei verschiedenen Zahlen zu sehen, und das ist eine Luege im Bild.
    skala = max([w for w in (d["glatt"], d["roh"]) if w] or [0])
    d["glatt_p"] = _balken(d["glatt"], skala)
    d["roh_p"] = _balken(d["roh"], skala)
    return d


# ------------------------------------- 3 Was die Bloecke wirklich verlangt haben
def _block(stats, hoehe):
    """Ein Block als Eintrittspreis. Liefert IMMER alle Schluessel.

    Gemessen wird minfeerate: die niedrigste Gebuehr, die noch hineinkam.

    ⚠️ Sie ist regelmaessig zu niedrig zu lesen: eine Transaktion mit 1 sat/vB
    kommt mit hinein, wenn ein teures Kind sie mitzieht (CPFP). Der Miner hat
    dann nicht 1 sat/vB akzeptiert, sondern den gemeinsamen Satz von Eltern und
    Kind. Der Kasten sagt das als Fussnote dazu, statt die Zahl stillschweigend
    zu ersetzen - wer sie nachrechnen will, findet genau diesen Wert in
    getblockstats wieder. (Die belastbarere Variante, das 10. Perzentil des
    Blockgewichts, steht auf der Gebuehrenseite; hier waere sie eine zweite
    Zahl in einer Kachel, die eine einzige Aussage tragen soll.)

    `hoehe` wird durchgereicht, damit ein Block seinen Platz im Band behaelt,
    wenn getblockstats fuer ihn ausgefallen ist. Ihn wegzulassen wuerde das Band
    stauchen, und die Luecke saehe aus wie ein kuerzeres Fenster.
    """
    b = {"hoehe": hoehe, "kurz": None, "zeit": None, "ist": None,
         "faktor": None, "ueber": False, "ist_p": None, "soll_p": None}
    if isinstance(stats, dict):
        # Kein "or": Hoehe 0 ist der Genesis-Block und eine gueltige Antwort.
        gemeldet = _zahl(stats.get("height"))
        b["hoehe"] = int(gemeldet) if gemeldet is not None else hoehe
        b["zeit"] = _zahl(stats.get("time"))
        b["ist"] = _zahl(stats.get("minfeerate"))
    # Die letzten drei Ziffern als Beschriftung unter der Saeule. Sechsstellige
    # Hoehen nebeneinander sind auf einem Handy nicht mehr zu lesen; die volle
    # Hoehe steht im title. Ziffern brauchen keinen Katalog - sie sind in
    # beiden Sprachen dieselben (wie die Stoppuhr in spiel.py).
    if b["hoehe"] is not None:
        b["kurz"] = "%03d" % (int(b["hoehe"]) % 1000)
    return b


async def _blockreihe(tor, spitze):
    """getblockstats fuer die letzten BLOCKFENSTER Bloecke, aeltester zuerst.

    Ueber die HOEHE und nicht ueber den Hash: das spart acht getblockhash und
    ist hier ungefaehrlich. Faellt zwischen zwei Aufrufen eine Reorganisation,
    bekommen wir den Block der neuen besten Kette - fuer eine Gebuehrenstatistik
    ist genau das der richtige. (Auf der Blockseite ist es umgekehrt: dort geht
    es um EINEN bestimmten Block, und der darf nicht getauscht werden.)
    """
    if spitze is None:
        return []
    hoehen = [h for h in range(int(spitze) - BLOCKFENSTER + 1, int(spitze) + 1)
              if h >= 0]
    if not hoehen:
        return []
    roh = await asyncio.gather(*[
        _sicher(tor, "getblockstats", h, FELDER) for h in hoehen])
    return [_block(s, h) for h, s in zip(hoehen, roh)]


def _vergleich(bloecke, prognose):
    """Die Rechnung, um die es geht: wie oft haette die Prognose nicht gereicht?

    Verglichen wird die AKTUELLE Schaetzung fuer den naechsten Block gegen die
    Eintrittspreise der letzten Bloecke. Das ist keine Rueckrechnung dessen, was
    damals vorhergesagt war - dazu muesste der Knoten seine alten Schaetzungen
    aufbewahren, und das tut er nicht. Es ist die ehrlichere Frage: wuerde die
    Zahl, die dir dein Knoten JETZT nennt, in die letzten Bloecke hineingereicht
    haben?

    Setzt nebenbei die Balkenlaengen je Block - beide Saeulen eines Paares an
    demselben Massstab, sonst waere das Bild bedeutungslos.
    """
    v = {"n": 0, "prognose": prognose, "genug": False, "nicht_gereicht": None,
         "nicht_p": None, "faktor_median": None, "faktor_max": None,
         "soll_p": None}

    werte = [b["ist"] for b in bloecke if b["ist"] is not None]
    v["n"] = len(werte)

    # Massstab: der teuerste gemessene Eintritt oder die Prognose, je nachdem,
    # was hoeher liegt. Sonst liefe die Prognosesaeule aus dem Bild - und genau
    # ihr Verhaeltnis zu den anderen ist die Aussage.
    skala = max(werte + ([prognose] if prognose else [])) if werte else prognose
    v["soll_p"] = _balken(prognose, skala)
    for b in bloecke:
        b["ist_p"] = _balken(b["ist"], skala)
        b["soll_p"] = v["soll_p"]
        if prognose and b["ist"] is not None:
            b["faktor"] = b["ist"] / prognose
            b["ueber"] = b["ist"] > prognose

    if not prognose or len(werte) < MINDESTBLOECKE:
        return v

    v["genug"] = True
    v["nicht_gereicht"] = sum(1 for w in werte if w > prognose)
    v["nicht_p"] = _anteil(v["nicht_gereicht"], len(werte))
    faktoren = [w / prognose for w in werte]
    v["faktor_median"] = _median(faktoren)
    v["faktor_max"] = max(faktoren)
    return v


# ------------------------------------------- 4 Konkurrierende Kettenspitzen
def _spitzen(roh):
    """getchaintips zusammenfassen. None, wenn der Aufruf ausgefallen ist.

    Bewusst eine eigene, kleine Fassung statt eines Imports aus knotenseite.py:
    dort gehoert sie zu einer ganzen Seite mit anderem Zuschnitt (sechs Zeilen,
    andere Felder), und an dieser Datei arbeitet gerade jemand anderes. Zwanzig
    Zeilen Doppelung sind der Preis dafuer, dass dieser Kasten fuer sich steht.
    """
    if roh is None:
        return None
    tips = [t for t in roh if isinstance(t, dict)]
    aktiv = next((t for t in tips if t.get("status") == "active"), None)
    zweige = [t for t in tips if t is not aktiv]
    zweige.sort(key=lambda t: (_zahl(t.get("height")) or 0), reverse=True)

    def eintrag(t):
        h = t.get("hash") if isinstance(t.get("hash"), str) else None
        stand = t.get("status")
        return {
            "hoehe": _zahl(t.get("height")),
            "laenge": _zahl(t.get("branchlen")),
            "stand": stand,
            # None heisst: kein Erklaertext im Katalog, die Vorlage zeigt den
            # Rohwert. Ein sichtbares "!tips.state.xyz!" waere schlimmer.
            "stand_schluessel": stand if stand in SPITZEN_ZUSTAENDE else None,
            # Die LETZTEN Zeichen: die ersten sechzehn eines Blockhashs sind
            # heute samt und sonders Nullen und unterscheiden gar nichts.
            "kurz": ("…" + h[-12:]) if h and len(h) > 12 else h,
        }

    return {
        "anzahl": len(tips),
        "zweige": len(zweige),
        "aktiv_hoehe": _zahl((aktiv or {}).get("height")),
        "liste": [eintrag(t) for t in zweige[:SPITZEN_ZEILEN]],
        "weitere": max(0, len(zweige) - SPITZEN_ZEILEN),
    }


_spitzen_puffer = {"wert": None, "erhoben": 0.0, "ms": None}
_spitzen_schloss = asyncio.Lock()


async def _spitzen_holen(tor):
    """getchaintips mit Zeitpuffer - der teuerste Aufruf dieses Kastens.

    114 ms sind das Zehnfache jedes anderen Aufrufs hier. Auf der Startseite,
    die man neu laedt, ohne darueber nachzudenken, waere das eine unnoetige
    Dauerlast auf einem Knoten, auf dem echtes Geld liegt. Die Sperre
    verhindert, dass gleichzeitige Anfragen alle gemeinsam durchrutschen.

    Gibt (Rohantwort, gemessene Millisekunden) zurueck; die Millisekunden
    gehoeren zum gepufferten Wert, sonst stuende bei jedem zweiten Aufruf
    "0 ms" da - was zwar stimmt, aber die falsche Frage beantwortet.
    """
    jetzt = time.monotonic()
    if (_spitzen_puffer["wert"] is not None
            and jetzt - _spitzen_puffer["erhoben"] < SPITZEN_TTL):
        return _spitzen_puffer["wert"], _spitzen_puffer["ms"]
    async with _spitzen_schloss:
        jetzt = time.monotonic()
        if (_spitzen_puffer["wert"] is not None
                and jetzt - _spitzen_puffer["erhoben"] < SPITZEN_TTL):
            return _spitzen_puffer["wert"], _spitzen_puffer["ms"]
        start = time.monotonic()
        wert = await _sicher(tor, "getchaintips")
        ms = (time.monotonic() - start) * 1000.0
        if wert is not None:
            _spitzen_puffer.update({"wert": wert, "erhoben": time.monotonic(),
                                    "ms": ms})
        return wert, ms


# ------------------------------------------- 5 Kein Nachhausetelefonieren
# Was als "laedt etwas von aussen" gilt: ein Verweis, den ein Browser wirklich
# holt. Der Musterausdruck verlangt deshalb den EINLEITER (src=, href=, url(,
# @import, fetch() und dazu ein "//" - eine nackte Adresse in einem Kommentar
# ist kein Aufruf, und der Namensraum xmlns="http://www.w3.org/..." wird von
# keinem Browser je abgerufen. Beides zaehlt hier deshalb bewusst NICHT mit;
# wer die Zusage nachpruefen will, sucht mit genau diesem Ausdruck.
_HOLT_VON_AUSSEN = re.compile(
    r"""(?:\b(?:src|href|xlink:href)\s*=\s*["']?"""
    r"""|@import\s+(?:url\()?\s*["']?"""
    r"""|\burl\(\s*["']?"""
    r"""|\bfetch\(\s*["']?)"""
    r"""\s*(?:https?:)?//""", re.I)

# Nur ausgelieferte Textdateien. Bilder koennen keine Verweise nachladen, und
# ein .pyc gehoert nicht in die Auslieferung.
_GEPRUEFT = (".html", ".css", ".js", ".svg", ".json", ".txt", ".webmanifest")

_stille_puffer = {"wert": None}

# Farbe je Einordnung. Sie steht hier und nicht in der Vorlage, damit sie sich
# im Selbsttest nachpruefen laesst - eine Bedingung im HTML kann das nicht.
_ART_KLASSE = {"loopback": "gut", "privat": "gut", "fremd": "schlecht",
               "unbekannt": "warn", "aus": "neutral"}


def _netzart(host):
    """Wohin ein Ziel zeigt - ohne DNS zu fragen.

    ⚠️ Ein Name wird ABSICHTLICH nicht aufgeloest: eine DNS-Abfrage waere genau
    der Aufruf nach draussen, dessen Abwesenheit diese Kachel behauptet. Was
    sich nicht ohne Netz entscheiden laesst, heisst hier "unbekannt" - und
    "unbekannt" zaehlt nicht als "im eigenen Netz".

    ⚠️ "aus" ist etwas ANDERES als "unbekannt": ein nicht eingerichtetes Ziel
    wird nie angesprochen. Es als ungeklaert zu zaehlen, hat die Zusage dieser
    Kachel gekippt, obwohl gerade dann besonders wenig hinausgeht - der Fehler
    fiel beim Rendern ohne Electrum-Server auf.
    """
    if not host:
        return "aus"
    h = host.strip().strip("[]")
    if h.lower() in ("localhost", "localhost.localdomain"):
        return "loopback"
    try:
        a = ipaddress.ip_address(h)
    except ValueError:
        # mDNS: ein .local-Name wird per Multicast im eigenen Netz aufgeloest
        # und verlaesst es nie. Umbrel liefert seine Apps genau so aus.
        return "privat" if h.lower().endswith(".local") else "unbekannt"
    if a.is_loopback:
        return "loopback"
    if a.is_private or a.is_link_local:
        return "privat"
    return "fremd"


def _dateien_pruefen(wurzeln):
    """(Anzahl gepruefter Dateien, Anzahl Verweise nach draussen)."""
    dateien = treffer = 0
    for wurzel in wurzeln:
        for ordner, _, namen in os.walk(wurzel):
            for name in sorted(namen):
                if not name.lower().endswith(_GEPRUEFT):
                    continue
                dateien += 1
                try:
                    with open(os.path.join(ordner, name),
                              encoding="utf-8", errors="replace") as f:
                        inhalt = f.read()
                except OSError:
                    # Nicht lesbar heisst nicht geprueft. Sie faellt aus der
                    # Zaehlung heraus, statt als "sauber" zu gelten.
                    dateien -= 1
                    continue
                treffer += len(_HOLT_VON_AUSSEN.findall(inhalt))
    return dateien, treffer


def stille(erneut=False):
    """Die pruefbare Zusage: nichts verlaesst diese Maschine.

    Zwei Belege, beide gemessen und keiner behauptet:

    * Die ausgelieferten Dateien holen nichts von aussen. Gezaehlt wird ueber
      alle Vorlagen und statischen Dateien - kein CDN, keine Schriftart, kein
      Zaehlpixel. Wer es nachpruefen will, sucht mit _HOLT_VON_AUSSEN.
    * Die Ziele, mit denen die App ueberhaupt spricht (das RPC des Knotens und
      der Electrum-Index), werden eingeordnet: eigene Maschine, eigenes Netz
      oder draussen. Gelesen wird die Umgebung - ohne DNS, ohne Verbindung.
      Liegt eines draussen, sagt die Kachel das, statt die Zusage zu wiederholen.

    ⚠️ Was das NICHT beweist: dass kein Python-Modul irgendwo einen eigenen
    Aufruf macht. Das steht in der Bauart (httpx spricht nur mit dem RPC-Tor,
    elektrum.py nur mit einer TCP-Zeile) und laesst sich zur Laufzeit nicht
    messen. Die Kachel behauptet deshalb genau so viel, wie sie gezeigt hat.

    Das Ergebnis wird gepuffert: Umgebung und Auslieferung aendern sich
    waehrend eines Prozesslebens nicht, und die Startseite soll nicht bei jedem
    Aufruf drei Dutzend Dateien lesen.
    """
    if _stille_puffer["wert"] is not None and not erneut:
        return _stille_puffer["wert"]

    hier = os.path.dirname(__file__)
    dateien, treffer = _dateien_pruefen(
        (os.path.join(hier, "statisch"), os.path.join(hier, "vorlagen")))

    ziele = []
    for schluessel, host in (
            ("mehrwert.still.rpc",
             os.environ.get("BITCOIN_RPC_HOST", "127.0.0.1")),
            ("mehrwert.still.electrum", elektrum.ziel()[0])):
        art = _netzart(host)
        ziele.append({"schluessel": schluessel, "art": art,
                      "klasse": _ART_KLASSE[art]})

    # Gezaehlt werden nur Ziele, die es ueberhaupt gibt. Ein nicht
    # eingerichteter Electrum-Server ist kein halbes Ziel, sondern keines.
    benutzt = [z for z in ziele if z["art"] != "aus"]
    daheim = [z for z in benutzt if z["art"] in ("loopback", "privat")]
    d = {
        "dateien": dateien,
        "fremdquellen": treffer,
        "sauber": treffer == 0,
        "ziele": ziele,
        "daheim": len(daheim),
        "ziele_gesamt": len(benutzt),
    }
    # Drei Zustaende, und die Kachel sagt genau den, der zutrifft:
    #   gut     - nichts verlaesst die Maschine, alles nachgezaehlt
    #   offen   - ein Ziel ist ein NAME. Wo er hinzeigt, wuesste man erst nach
    #             einer DNS-Abfrage - und genau die machen wir nicht.
    #   kaputt  - ein Ziel liegt draussen oder eine Datei laedt von aussen.
    #             Dann steht hier eine Warnung und kein Haken: lieber ein
    #             sichtbarer Widerspruch als eine Zusage, die nicht mehr gilt.
    if not d["sauber"] or any(z["art"] == "fremd" for z in benutzt):
        d["zustand"], d["klasse"] = "kaputt", "schlecht"
        d["satz"] = "mehrwert.still.warnung"
    elif any(z["art"] == "unbekannt" for z in benutzt):
        d["zustand"], d["klasse"] = "offen", "warn"
        d["satz"] = "mehrwert.still.offen"
    else:
        d["zustand"], d["klasse"] = "gut", "gut"
        d["satz"] = "mehrwert.still.zusage"
    _stille_puffer["wert"] = d
    return d


# --------------------------------------------------------------- Hauptweg
def _leer(erreichbar=False):
    """Immer derselbe Satz Schluessel - auch wenn nichts erhoben werden konnte."""
    return {
        "erreichbar": erreichbar,
        "spitze": None,
        "fenster": BLOCKFENSTER,
        "index": _index_stand(None, None, eingerichtet=False),
        "schaetzer": _schaetzer(None, None),
        "bloecke": [],
        "vergleich": _vergleich([], None),
        "spitzen": None,
        "spitzen_ms": None,
        "still": stille(),
    }


_block_puffer = {"hash": None, "liste": [], "erhoben": 0.0}
_block_schloss = asyncio.Lock()


async def _blockreihe_gepuffert(tor, spitze, spitzen_hash):
    """Das Blockfenster, gepuffert am SPITZENHASH und nicht an der Hoehe.

    Bei einer Reorganisation bleibt die Hoehe gleich, waehrend sich der Inhalt
    aendert. Ein Puffer, der auf die Hoehe hoert, wuerde den verwaisten Block
    weiterzeigen - unbemerkt, weil alle Zahlen plausibel aussehen. (Dieselbe
    Begruendung wie in kette.py.)

    Gibt KOPIEN heraus: der Aufrufer setzt Balkenlaengen und Faktoren in die
    Bloecke hinein, und die haengen an der Prognose von JETZT. Wuerde er den
    Pufferinhalt selbst beschreiben, traegen zwei gleichzeitige Anfragen ihre
    Zahlen ineinander.
    """
    if spitze is None:
        return []
    async with _block_schloss:
        jetzt = time.monotonic()
        vollstaendig = bool(_block_puffer["liste"]) and all(
            b["ist"] is not None for b in _block_puffer["liste"])
        passt = (spitzen_hash is not None
                 and spitzen_hash == _block_puffer["hash"]
                 and _block_puffer["liste"])
        # Eine unvollstaendige Erhebung darf sich nicht bis zum naechsten Block
        # einbrennen: nach NACHFASSEN Sekunden wird es noch einmal versucht.
        if passt and (vollstaendig
                      or jetzt - _block_puffer["erhoben"] < NACHFASSEN):
            return [dict(b) for b in _block_puffer["liste"]]
        liste = await _blockreihe(tor, spitze)
        _block_puffer.update({"hash": spitzen_hash, "liste": liste,
                              "erhoben": jetzt})
        return [dict(b) for b in liste]


def leeren():
    """Puffer vergessen - fuer Tests. Im Betrieb ruft das niemand."""
    _block_puffer.update({"hash": None, "liste": [], "erhoben": 0.0})
    _spitzen_puffer.update({"wert": None, "erhoben": 0.0, "ms": None})
    _stille_puffer["wert"] = None


async def seite(tor, ziel=None):
    """Alles, was teile/mehrwert.html braucht - in einem Aufruf.

    Zwei Runden: erst die Kettenspitze (ohne sie wissen wir nicht, welche
    Bloecke zu holen sind), dann alles Uebrige nebenlaeufig. Die Seite wartet
    damit auf die langsamste Quelle statt auf die Summe.

    Schweigt der Knoten, wird NICHT abgebrochen: die Indexhoehe und die
    Stille-Zusage haengen nicht an ihm, und was gemessen werden kann, soll
    gemessen werden. Die uebrigen Kacheln zeigen dann Striche.

    `ziel` reicht nur der Selbsttest durch (siehe _index).
    """
    kette = await _sicher(tor, "getblockchaininfo")
    spitze = _zahl((kette or {}).get("blocks"))
    spitze = int(spitze) if spitze is not None else None
    spitzen_hash = (kette or {}).get("bestblockhash")
    if not isinstance(spitzen_hash, str):
        spitzen_hash = None

    index, glatt, roh, bloecke, tips = await asyncio.gather(
        _index(spitze, ziel),
        _sicher(tor, "estimatesmartfee", ZIEL),
        _sicher(tor, "estimaterawfee", ZIEL),
        _blockreihe_gepuffert(tor, spitze, spitzen_hash),
        _spitzen_holen(tor),
    )
    spitzen_roh, spitzen_ms = tips

    schaetzer = _schaetzer(glatt, roh)
    d = _leer(erreichbar=kette is not None)   # gleicher Schluesselsatz, gefuellt
    d.update({
        "spitze": spitze,
        "index": index,
        "schaetzer": schaetzer,
        "bloecke": bloecke,
        # Verglichen wird gegen die GEGLAETTETE Schaetzung: das ist die Zahl,
        # die eine Wallet anzeigt und die ein Nutzer wirklich bezahlt.
        "vergleich": _vergleich(bloecke, schaetzer["glatt"]),
        "spitzen": _spitzen(spitzen_roh),
        "spitzen_ms": spitzen_ms,
    })
    return d


# --------------------------------------------------------------- Selbsttest
def _selbsttest():
    """Die Rechnung dieses Moduls ohne Knoten und ohne Netz pruefen:

        python3 -m satscope.mehrwert

    Bewusst hier und nicht in selbsttest.py: an dieser Datei arbeitet gerade
    jemand anders (sie steht auf der Sperrliste dieses Auftrags). Sobald der
    Umbau vorbei ist, gehoert der Aufruf dort hinein.
    """
    fehler = []

    def p(name, ist, soll):
        if ist != soll:
            fehler.append("%s: %r erwartet, %r bekommen" % (name, soll, ist))
            print("  FEHLER  %s" % name)
        else:
            print("  ok      %s" % name)

    print("Einheiten")
    p("BTC/kvB -> sat/vB", _sat_pro_vb(0.00001), 1.0)
    p("keine Antwort ist keine Null", _sat_pro_vb(-1), None)
    p("bool ist keine Zahl", _sat_pro_vb(True), None)
    p("sat/kvB -> sat/vB", _eimer_sat_vb(2500), 2.5)
    p("Unendlich-Ersatzwert faellt weg", _eimer_sat_vb(1e99), None)

    print("\nKnoten gegen Index")
    p("einig", _index_stand(900000, 900000)["zustand"], "gleich")
    p("einig ist gut", _index_stand(900000, 900000)["klasse"], "gut")
    p("ein Block zurueck ist normal",
      _index_stand(900001, 900000)["zustand"], "kurz")
    p("zwei Bloecke sind ein Befund",
      _index_stand(900002, 900000)["zustand"], "zurueck")
    p("und der ist rot", _index_stand(900002, 900000)["klasse"], "schlecht")
    p("Abstand wird gezaehlt", _index_stand(900002, 900000)["abstand"], 2)
    p("Index voraus", _index_stand(900000, 900001)["zustand"], "voraus")
    p("Index stumm", _index_stand(900000, None)["zustand"], "stumm")
    p("gar kein Index-Server",
      _index_stand(900000, None, eingerichtet=False)["zustand"], "kein_server")
    p("Knoten stumm", _index_stand(None, 900000)["zustand"], "unbekannt")
    p("ohne Messung keine Zahl", _index_stand(None, 900000)["abstand"], None)
    p("Textschluessel wandert mit",
      _index_stand(900000, 900000)["satz"], "mehrwert.index.zustand.gleich")

    print("\nZwei Schaetzer nebeneinander")
    s = _schaetzer({"feerate": 0.00002, "blocks": 1},
                   {"short": {"feerate": 0.00001, "decay": 0.962, "scale": 1,
                              "pass": {"startrange": 1000, "endrange": 2500,
                                       "withintarget": 900.0,
                                       "totalconfirmed": 950.0,
                                       "inmempool": 30.0,
                                       "leftmempool": 20.0}}})
    p("geglaettete Zahl", s["glatt"], 2.0)
    p("rohe Zahl", s["roh"], 1.0)
    p("Faktor", s["faktor"], 2.0)
    p("Abweichung in Prozent", s["abweichung_p"], 100.0)
    p("Eimer unten", s["von_sat_vb"], 1.0)
    p("Eimer oben", s["bis_sat_vb"], 2.5)
    p("Trefferquote", round(s["erfolg_p"], 1), 90.0)
    p("groesserer Balken ist voll", s["glatt_p"], 100.0)
    p("kleinerer Balken ist halb", s["roh_p"], 50.0)
    s2 = _schaetzer(None, None)
    p("ohne Antworten kein Vergleich", s2["beide"], False)
    p("ohne Antworten keine Balken", s2["glatt_p"], None)
    p("Schluesselsatz bleibt gleich", sorted(s) == sorted(s2), True)
    s3 = _schaetzer({"feerate": 0.00002, "blocks": 3}, None)
    p("anderes Ziel wird gemeldet", s3["anderes_ziel"], True)

    print("\nPrognose gegen Wirklichkeit")
    reihe = [_block({"height": 100 + i, "minfeerate": w, "time": 1}, 100 + i)
             for i, w in enumerate((1.0, 2.0, 4.0, 8.0))]
    v = _vergleich(reihe, 2.0)
    p("wie viele haetten nicht gereicht", v["nicht_gereicht"], 2)
    p("in Prozent", v["nicht_p"], 50.0)
    p("Median-Faktor", v["faktor_median"], 1.5)
    p("schlimmster Faktor", v["faktor_max"], 4.0)
    p("Saeule des teuersten Blocks ist voll", reihe[3]["ist_p"], 100.0)
    p("Prognosesaeule an derselben Skala", v["soll_p"], 25.0)
    p("teurer als die Prognose ist markiert", reihe[3]["ueber"], True)
    p("billiger nicht", reihe[0]["ueber"], False)
    p("ohne Prognose kein Urteil", _vergleich(reihe, None)["nicht_gereicht"], None)
    p("zu wenige Bloecke, kein Urteil",
      _vergleich(reihe[:2], 2.0)["nicht_gereicht"], None)
    p("ausgefallener Block behaelt seinen Platz",
      _block(None, 777)["hoehe"], 777)
    p("und zeigt keine Zahl", _block(None, 777)["ist"], None)
    p("Kurzform der Hoehe", _block(None, 962007)["kurz"], "007")

    print("\nKettenspitzen")
    sp = _spitzen([
        {"height": 900000, "hash": "0" * 52 + "aaaabbbbcccc",
         "branchlen": 0, "status": "active"},
        {"height": 899998, "hash": "0" * 52 + "111122223333",
         "branchlen": 1, "status": "valid-fork"},
    ])
    p("alle Spitzen gezaehlt", sp["anzahl"], 2)
    p("die aktive zaehlt nicht als Zweig", sp["zweige"], 1)
    p("Kurzform zeigt das Ende", sp["liste"][0]["kurz"], "…111122223333")
    p("bekannter Zustand bekommt einen Schluessel",
      sp["liste"][0]["stand_schluessel"], "valid-fork")
    p("unbekannter Zustand bekommt keinen",
      _spitzen([{"status": "brandneu"}])["liste"][0]["stand_schluessel"], None)
    p("ausgefallener Aufruf", _spitzen(None), None)
    p("Muell wird ignoriert", _spitzen(["Unsinn", None])["anzahl"], 0)

    print("\nKein Nachhausetelefonieren")
    p("Loopback", _netzart("127.0.0.1"), "loopback")
    p("localhost", _netzart("localhost"), "loopback")
    p("eigenes Netz", _netzart("192.168.178.67"), "privat")
    p("auch 10.x", _netzart("10.0.0.5"), "privat")
    p("IPv6-Loopback", _netzart("::1"), "loopback")
    p("IPv6 in Klammern", _netzart("[fd00::1]"), "privat")
    p("mDNS bleibt im Haus", _netzart("umbrel.local"), "privat")
    p("oeffentliche Adresse faellt auf", _netzart("8.8.8.8"), "fremd")
    p("Name ohne DNS bleibt offen", _netzart("beispiel.org"), "unbekannt")
    # ⚠️ Der Fehler, der beim Rendern auffiel: ein nicht eingerichtetes Ziel
    # ist KEIN ungeklaertes Ziel. Es hatte die ganze Zusage gekippt.
    p("nichts eingerichtet ist kein Ziel", _netzart(None), "aus")
    p("und auch kein leerer String", _netzart(""), "aus")
    # ⚠️ Der eigentliche Beweis: die ausgelieferten Dateien holen nichts von
    # aussen. Schlaegt genau diese Zeile fehl, ist eine externe Quelle in das
    # Projekt geraten - dann stimmt die Zusage der Kachel nicht mehr.
    st = stille(erneut=True)
    p("keine Fremdquelle in der Auslieferung", st["fremdquellen"], 0)
    p("es wurden ueberhaupt Dateien geprueft", st["dateien"] > 10, True)
    p("ein Ziel wird nie doppelt gezaehlt",
      st["ziele_gesamt"] <= len(st["ziele"]), True)
    p("gezaehlt wird nur, was eingerichtet ist",
      st["ziele_gesamt"], sum(1 for z in st["ziele"] if z["art"] != "aus"))
    p("jedes Ziel traegt seine Farbe",
      sorted({z["klasse"] for z in st["ziele"]}) and all(
          z["klasse"] in ("gut", "warn", "schlecht", "neutral")
          for z in st["ziele"]), True)
    p("Zustand und Satz passen zusammen",
      st["satz"], {"gut": "mehrwert.still.zusage",
                   "offen": "mehrwert.still.offen",
                   "kaputt": "mehrwert.still.warnung"}[st["zustand"]])
    p("ein CDN wuerde auffallen",
      len(_HOLT_VON_AUSSEN.findall('<script src="https://cdn.x/y.js">')), 1)
    p("schuetzendes Muster: eigene Pfade zaehlen nicht",
      len(_HOLT_VON_AUSSEN.findall('<link href="/statisch/a.css">')), 0)
    p("Namensraum ist kein Aufruf",
      len(_HOLT_VON_AUSSEN.findall('<svg xmlns="http://www.w3.org/2000/svg">')), 0)

    print("\nGanze Seite am Probeknoten (kein Netz, kein Knoten)")
    from .probeknoten import ProbeTor
    from .rpc import BILLIG
    leeren()
    tor = ProbeTor()
    # ("", "") heisst: kein Electrum-Server eingerichtet. Damit laeuft der
    # Selbsttest auch dann ohne Netz, wenn ELECTRUM_HOST gesetzt ist.
    d = asyncio.run(seite(tor, ziel=("", "")))
    benutzt = sorted({m for m, _ in tor.aufrufe})
    p("keine teure Methode", sorted(set(benutzt) - set(BILLIG)), [])
    p("Aufrufzahl bleibt im Rahmen", len(tor.aufrufe) <= 12, True)
    p("Schluesselsatz vollstaendig", sorted(d) == sorted(_leer()), True)
    p("Knoten war erreichbar", d["erreichbar"], True)
    p("acht Bloecke im Fenster", len(d["bloecke"]), BLOCKFENSTER)
    p("Zweige gefunden", d["spitzen"]["zweige"], 2)
    p("beide Schaetzer da", d["schaetzer"]["beide"], True)
    p("ohne Electrum-Server kein erfundener Abstand",
      d["index"]["abstand"], None)
    # Zweiter Aufruf: das Blockfenster muss aus dem Puffer kommen, sonst liegt
    # bei jedem F5 wieder eine Viertelsekunde Knotenzeit auf dem Tisch.
    vorher = len(tor.aufrufe)
    asyncio.run(seite(tor, ziel=("", "")))
    p("Bloecke kommen aus dem Puffer",
      len(tor.aufrufe) - vorher <= 4, True)
    leeren()

    print("\n%d Fehler" % len(fehler))
    for f in fehler:
        print("  " + f)
    return 1 if fehler else 0


if __name__ == "__main__":
    import sys

    sys.exit(_selbsttest())
