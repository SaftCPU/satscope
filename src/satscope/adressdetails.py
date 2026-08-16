"""Die Adressseite, zweite Schicht: woraus der Saldo besteht.

Die erste Schicht (elektrum.adress_uebersicht) beantwortet "wie viel" und "wie
oft". Dieses Modul beantwortet die Fragen, die danach kommen:

    Woraus besteht der Saldo?  Ein halbes Bitcoin kann ein Stueck sein oder
                               vierhundert - beim Ausgeben ist das der
                               Unterschied zwischen 68 und 27.200 vByte.
    Seit wann gibt es sie?     blockchain.scripthash.get_first_use
    Was lohnt nicht mehr?      Ausgaenge, deren Ausgabe bei der aktuellen
                               Gebuehr mehr kostet als sie wert sind.
    Wie sieht sie aus?         QR-Code, im Haus gerechnet (unten im Modul).

Wie ueberall in Satscope ist JEDER Wert einzeln abgesichert: faellt Electrum
aus, fehlen die offenen Ausgaenge und die Adressseite steht trotzdem; faellt
nur get_first_use aus (electrs kennt die Methode nicht), fehlt genau diese eine
Zeile. Geraten wird nichts.

KOSTEN, an Fulcrum auf .67 gemessen (16.08.2026):
    blockchain.scripthash.listunspent
             50 Ausgaenge   11 ms
         78.221 Ausgaenge  320 ms warm - aber 14,8 s beim ERSTEN Aufruf nach
                           dem Start des Servers (kalter Index)
    blockchain.scripthash.get_first_use            12 ms
RPC dazu (siehe rpc.py): getblockchaininfo 9 ms, estimatesmartfee 7 ms,
getblockhash <1 ms, getblockheader <5 ms. Alles nebenlaeufig, das Tor deckelt
selbst auf vier gleichzeitige Aufrufe.

⚠️ Ein teurer Aufruf kommt hier NICHT vor. Der naheliegende waere
`scantxoutset` gewesen - er kann dasselbe aus dem UTXO-Satz des Knotens
beantworten und braucht dort gemessene 48 Sekunden. Alles unten stammt
deshalb aus dem Electrum-Index, der genau dafuer gebaut ist.
"""
import asyncio
import time

from . import elektrum
from .rpc import RpcFehler

# Ab so vielen Bewegungen wird listunspent gar nicht erst gefragt. Die Grenze
# liegt bewusst hoch: die groesste Adresse der Kette (die Spendenadresse aus
# dem Genesis-Block, 65.311 Bewegungen, 78.221 offene Ausgaenge) liefert warm
# in 0,32 s. Darueber wird es absurd, und absurd muss die Seite nicht koennen.
MAX_BEWEGUNGEN = 100000

# So viele Ausgaenge werden einzeln aufgelistet. Die Zusammenfassung darueber
# bleibt EXAKT - sie wird ueber ALLE gerechnet, nicht ueber die ersten 200.
LISTENGRENZE = 200

# Eigenes, knappes Zeitlimit fuer diese Zusatzschicht. Es ist der eigentliche
# Schutz, nicht die Bewegungsgrenze: dieselbe Abfrage, die warm 0,32 s braucht,
# brauchte beim ERSTEN Aufruf nach dem Serverstart 14,8 s (kalter Index,
# gemessen 16.08.2026). Laeuft das Limit ab, zeigt die Seite hier einen Strich
# und steht im Uebrigen - beim naechsten Aufruf ist der Index warm.
ZEITLIMIT = 10.0

# Was ein Eingang beim Ausgeben wiegt, in vByte - der Standard-Ausgabeweg der
# jeweiligen Adressart:
#   P2PKH   36 Aussenpunkt + 1 + 107 scriptSig + 4 Sequenz            = 148
#   P2WPKH  41 Basis + 27/4 Zeuge                                     =  68
#   P2TR    41 Basis + 16,5/4 Zeuge (Schluesselweg), aufgerundet      =  58
#
# ⚠️ P2SH und P2WSH fehlen ABSICHTLICH. Was dort beim Ausgeben anfaellt, steht
# im Skript und nicht in der Adresse - ein P2SH kann ein eingepacktes SegWit
# sein oder ein 3-aus-5-Multisig mit dem vierfachen Gewicht. Lieber gar keine
# Staubgrenze als eine erfundene; fuer diese Adressarten entfaellt sie.
EINGANG_VB = {"P2PKH": 148, "P2WPKH": 68, "P2TR": 58}

# Zwei Gebuehrenziele: was das Ausgeben JETZT kostet (naechster Block) und was
# es kostet, wenn man einen Tag Geduld hat. Genau dazwischen liegt die einzige
# Handlungsmoeglichkeit, die der Nutzer hier ueberhaupt hat.
ZIEL_JETZT = 1
ZIEL_TAG = 144

# Groessenklassen der Ausgaenge, in Satoshi. Zehnerschritte, weil UTXO-Bestaende
# ueber Groessenordnungen streuen: Staub bei 500 sat und ein Batzen bei 5 BTC
# stehen in derselben Liste.
KLASSEN_GRENZEN = (1000, 10000, 100000, 1000000, 10000000, 100000000, 1000000000)
KLASSEN_NAMEN = ("<1k", "1k", "10k", "100k", "1M", "10M", "100M", "1G+")


# --------------------------------------------------------------- Werkzeug
async def _sicher(tor, methode, *argumente):
    """Ruft auf und liefert None statt zu werfen - wie knoten._sicher().

    Bewusst noch einmal hier und nicht importiert: der Strich statt einer
    erfundenen Zahl ist die wichtigste Regel dieses Projekts, und sie soll in
    jedem Modul lesbar dastehen, das sie anwendet.
    """
    try:
        return await tor.ruf(methode, *argumente)
    except (RpcFehler, OSError, asyncio.TimeoutError):
        return None


async def _nichts():
    """Platzhalter, damit asyncio.gather() eine feste Stellenzahl behaelt."""
    return None


def _datum(stempel):
    """Zeitstempel als UTC-Datum. Keine Ortszeit: der Server kennt die des
    Browsers nicht, und eine falsche Zeitzone ist schlimmer als eine fremde."""
    if not stempel:
        return None
    return time.strftime("%Y-%m-%d", time.gmtime(int(stempel)))


async def _blockzeit(tor, hoehe):
    """Zeitstempel eines Blocks. Zwei billige Aufrufe, zusammen unter 6 ms.

    Ohne diesen Umweg gaebe es das Alter eines Ausgangs nur in Bloecken. Zehn
    Minuten je Block waeren eine Annahme; der Kopf eines Blocks ist eine
    Messung, und die kostet hier fast nichts.
    """
    if hoehe is None or hoehe <= 0:
        return None
    h = await _sicher(tor, "getblockhash", int(hoehe))
    if not h:
        return None
    kopf = await _sicher(tor, "getblockheader", h)
    zeit = (kopf or {}).get("time")
    return int(zeit) if isinstance(zeit, int) else None


async def _rate(tor, ziel):
    """estimatesmartfee in sat/vB. None, wenn der Knoten nichts sagen will."""
    antwort = await _sicher(tor, "estimatesmartfee", ziel)
    wert = (antwort or {}).get("feerate")
    if not wert:
        return None
    try:
        # BTC/kvB -> sat/vB: 1e8 Satoshi je BTC, geteilt durch 1000 vByte.
        return float(wert) * 100000.0
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------- Electrum
async def offene_ausgaenge(kennung, bewegungen=None, zeitlimit=ZEITLIMIT):
    """listunspent und get_first_use in EINER Verbindung.

    Beides zusammen, weil der Handshake sonst zweimal bezahlt wird. Beide
    Antworten sind fuer sich abgesichert:
      * listunspent faellt aus  -> "utxos" ist None, die Seite zeigt Striche
      * get_first_use fehlt     -> "erst_bekannt" ist False; electrs kennt die
        Methode NICHT (nur Fulcrum), und das ist kein Fehler, sondern eine
        Eigenschaft des Servers.
    Ein Ergebnis None bei get_first_use bedeutet dagegen etwas anderes: die
    Adresse wurde nie benutzt. Beide Faelle duerfen nicht verwechselt werden.
    """
    host, port = elektrum.ziel()
    if not host or not port:
        return {"utxos": None, "grund": "kein_index",
                "erst": None, "erst_bekannt": False}

    # Der Deckel greift, BEVOR gefragt wird - eine Abfrage, die man nicht
    # stellt, kann den Index auch nicht aufhalten.
    zu_gross = bewegungen is not None and bewegungen > MAX_BEWEGUNGEN

    utxos, grund, erst, erst_bekannt = None, None, None, False
    try:
        async with elektrum.Verbindung(host, port, zeitlimit) as v:
            if not zu_gross:
                try:
                    utxos = await v.frage(
                        "blockchain.scripthash.listunspent", [kennung])
                except elektrum.ElektrumFehler:
                    grund = "abgelehnt"
            else:
                grund = "zu_gross"
            try:
                erst = await v.frage(
                    "blockchain.scripthash.get_first_use", [kennung])
                erst_bekannt = True
            except elektrum.ElektrumFehler:
                # Fulcrum kann es, electrs nicht. Kein Grund, irgendetwas
                # anderes auf dieser Seite zu verlieren.
                erst_bekannt = False
    except (OSError, asyncio.TimeoutError, ValueError, TypeError,
            elektrum.ElektrumFehler) as fehler:
        return {"utxos": utxos, "grund": grund or _grund(fehler),
                "erst": erst, "erst_bekannt": erst_bekannt}

    return {"utxos": utxos, "grund": grund,
            "erst": erst, "erst_bekannt": erst_bekannt}


def _grund(fehler):
    """Warum es keine Liste gibt - in einem Wort, das man erklaeren kann."""
    if isinstance(fehler, asyncio.TimeoutError):
        return "zu_langsam"
    return "kein_index"


# --------------------------------------------------------------- Auswertung
def _klassen(werte, staubgrenze=None):
    """Die Ausgaenge nach Groessenordnung sortiert - als Leiter.

    Die Balkenlaenge geht mit der Wurzel, nicht linear: fast jeder Bestand hat
    eine Klasse, in der fast alles liegt, und linear waeren alle anderen
    Nulllinien. Die genaue Zahl steht ohnehin daneben, die Form ist die
    Aussage.

    Als "Staub" wird eine Klasse nur markiert, wenn ihre GANZE Spanne unter der
    Grenze liegt. Eine halb betroffene Klasse einzufaerben waere eine Aussage
    ueber Ausgaenge, die gar nicht betroffen sind.
    """
    if not werte:
        return None
    eimer = [0] * (len(KLASSEN_GRENZEN) + 1)
    summen = [0] * (len(KLASSEN_GRENZEN) + 1)
    for w in werte:
        i = 0
        for grenze in KLASSEN_GRENZEN:
            if w < grenze:
                break
            i += 1
        eimer[i] += 1
        summen[i] += w
    hoechster = max(eimer) or 1
    raus = []
    for i, n in enumerate(eimer):
        oben = KLASSEN_GRENZEN[i] if i < len(KLASSEN_GRENZEN) else None
        raus.append({
            "i": i, "name": KLASSEN_NAMEN[i], "anzahl": n,
            "summe_sat": summen[i],
            "anteil": round((n / hoechster) ** 0.5, 4) if n else 0.0,
            "staub": bool(n) and staubgrenze is not None
                     and oben is not None and oben <= staubgrenze,
        })
    return raus


def _staub(werte, art, jetzt_vb, tag_vb):
    """Welche Ausgaenge kosten beim Ausgeben mehr, als sie wert sind?

    Die Rechnung ist eine Multiplikation und keine Schaetzung: ein Eingang
    dieser Adressart wiegt EINGANG_VB vByte, die Gebuehr dafuer ist
    vByte * sat/vB. Liegt der Ausgang darunter, verbrennt sein Einloesen mehr
    als er hergibt - er ist wirtschaftlich tot, auch wenn die Adressseite
    weiter einen Saldo anzeigt.

    Zwei Grenzen, weil dazwischen die einzige Handlung liegt, die dem Nutzer
    ueberhaupt bleibt: warten. Fehlt eine Zutat (unbekannte Adressart, keine
    Gebuehrenschaetzung), fehlt genau diese Grenze - nicht die ganze Ansicht.
    """
    vb = EINGANG_VB.get(art)
    if not vb or not werte:
        return None

    def klasse(rate):
        if rate is None:
            return None
        grenze = vb * rate
        betroffen = [w for w in werte if w < grenze]
        return {"vb": rate, "grenze_sat": grenze, "anzahl": len(betroffen),
                "summe_sat": sum(betroffen)}

    jetzt, tag = klasse(jetzt_vb), klasse(tag_vb)
    if jetzt is None and tag is None:
        return None
    # Fehlt ausgerechnet die Schaetzung fuer den naechsten Block, tritt die
    # fuer den Tag an ihre Stelle. Der Satz nennt die verwendete Rate selbst
    # mit - er bleibt also wahr, und die Aussage geht nicht verloren.
    if jetzt is None:
        jetzt = tag
    return {"eingang_vb": vb, "art": art, "jetzt": jetzt, "tag": tag}


def _median(werte):
    """Der mittlere Ausgang. Bei gerader Anzahl der untere der beiden -
    ein gemittelter Betrag waere ein Betrag, den es nicht gibt."""
    if not werte:
        return None
    geordnet = sorted(werte)
    return geordnet[(len(geordnet) - 1) // 2]


# --------------------------------------------------------------- Hauptweg
async def details(tor, kennung, adresse=None, art=None, bewegungen=None,
                  jetzt=None):
    """Alles, was die Teilvorlage adressdetails.html braucht.

    `kennung` ist Electrums scripthash (aus adresse.scripthash), `art` die
    Bezeichnung derselben Funktion ("P2WPKH", "P2TR", ...). `bewegungen` ist
    die bereits bekannte Anzahl Bewegungen aus elektrum.adress_uebersicht -
    sie entscheidet, ob listunspent ueberhaupt gefragt wird.
    """
    jetzt = int(time.time()) if jetzt is None else int(jetzt)

    # Electrum und der Knoten gleichzeitig. Nacheinander waere es die Summe
    # der Wartezeiten statt der laengsten.
    quelle, kette, jetzt_vb, tag_vb = await asyncio.gather(
        offene_ausgaenge(kennung, bewegungen),
        _sicher(tor, "getblockchaininfo"),
        _rate(tor, ZIEL_JETZT),
        _rate(tor, ZIEL_TAG),
    )

    spitze = (kette or {}).get("blocks")
    roh = quelle["utxos"]

    ergebnis = {
        "da": roh is not None,
        "grund": quelle["grund"],
        "grenze": LISTENGRENZE,
        "max_bewegungen": MAX_BEWEGUNGEN,
        "spitze": spitze,
        "jetzt": jetzt,
        "anzahl": None, "summe_sat": None, "kleinster_sat": None,
        "groesster_sat": None, "median_sat": None,
        "offen_anzahl": 0, "liste": [], "gekuerzt": False,
        "klassen": None, "staub": None,
        "aeltester": None, "juengster": None,
        "erst": None, "erst_bekannt": quelle["erst_bekannt"],
        "qr": qr_svg_daten(adresse) if adresse else None,
    }

    if roh is None:
        # Kein Saldo, keine Anzahl, keine Grenze - nur der Grund. Erfunden
        # wird hier nichts, auch keine Null. Die Erstbenutzung steht davon
        # unabhaengig da: sie kommt aus einem eigenen Aufruf.
        erst = _erst_aufbereiten(quelle["erst"], spitze)
        if erst:
            erst["zeit"] = await _blockzeit(tor, erst["hoehe"])
            erst["datum"] = _datum(erst["zeit"])
            erst["alter_s"] = (jetzt - erst["zeit"]) if erst["zeit"] else None
        ergebnis["erst"] = erst
        return ergebnis

    # Nur Eintraege mit Betrag zaehlen mit. Ein Eintrag ohne "value" waere ein
    # Formfehler des Servers, und ihn als 0 zu zaehlen wuerde die Summe
    # verfaelschen, ohne dass es jemand merkt.
    eintraege = [e for e in roh if isinstance(e, dict)
                 and isinstance(e.get("value"), int)]
    werte = [e["value"] for e in eintraege]

    # Neueste zuerst; unbestaetigte (Hoehe 0) ganz nach oben - sie sind das,
    # was sich gerade aendert.
    eintraege.sort(key=lambda e: (e.get("height") or 0), reverse=True)
    offen = [e for e in eintraege if (e.get("height") or 0) <= 0]
    bestaetigt = [e for e in eintraege if (e.get("height") or 0) > 0]

    jetzt_grenze = None
    staub = _staub(werte, art, jetzt_vb, tag_vb)
    if staub and staub["jetzt"]:
        jetzt_grenze = staub["jetzt"]["grenze_sat"]

    liste = []
    for e in (offen + bestaetigt)[:LISTENGRENZE]:
        hoehe = e.get("height") or 0
        liste.append({
            "txid": e.get("tx_hash"),
            "n": e.get("tx_pos"),
            "sat": e["value"],
            "hoehe": hoehe if hoehe > 0 else None,
            # Alter in Bloecken - exakt, ohne Annahme ueber die Blockzeit.
            "bestaetigungen": (spitze - hoehe + 1)
                              if (spitze is not None and hoehe > 0) else None,
            "staub": jetzt_grenze is not None and e["value"] < jetzt_grenze,
        })

    hoehen = [e["height"] for e in bestaetigt if e.get("height")]
    aelteste_hoehe = min(hoehen) if hoehen else None
    juengste_hoehe = max(hoehen) if hoehen else None

    # Drei Blockzeiten hoechstens (aeltester Ausgang, juengster, Erstbenutzung).
    # Je zwei billige Aufrufe - zusammen unter 20 ms.
    erst = _erst_aufbereiten(quelle["erst"], spitze)
    alt_zeit, jung_zeit, erst_zeit = await asyncio.gather(
        _blockzeit(tor, aelteste_hoehe),
        _blockzeit(tor, juengste_hoehe) if juengste_hoehe != aelteste_hoehe
        else _nichts(),
        _blockzeit(tor, erst["hoehe"]) if erst else _nichts(),
    )
    if juengste_hoehe == aelteste_hoehe:
        jung_zeit = alt_zeit
    if erst:
        erst["zeit"] = erst_zeit
        erst["datum"] = _datum(erst_zeit)
        erst["alter_s"] = (jetzt - erst_zeit) if erst_zeit else None

    ergebnis.update({
        "anzahl": len(eintraege),
        "summe_sat": sum(werte) if werte else 0,
        "kleinster_sat": min(werte) if werte else None,
        "groesster_sat": max(werte) if werte else None,
        "median_sat": _median(werte),
        "offen_anzahl": len(offen),
        "liste": liste,
        "gekuerzt": len(eintraege) > LISTENGRENZE,
        "klassen": _klassen(werte, jetzt_grenze),
        "staub": staub,
        "erst": erst,
        "aeltester": {"hoehe": aelteste_hoehe, "zeit": alt_zeit,
                      "datum": _datum(alt_zeit),
                      "alter_s": (jetzt - alt_zeit) if alt_zeit else None}
                     if aelteste_hoehe else None,
        "juengster": {"hoehe": juengste_hoehe, "zeit": jung_zeit,
                      "datum": _datum(jung_zeit),
                      "alter_s": (jetzt - jung_zeit) if jung_zeit else None}
                     if juengste_hoehe else None,
    })
    return ergebnis


def _erst_aufbereiten(antwort, spitze):
    """get_first_use in die Form bringen, die die Vorlage erwartet.

    Fulcrum antwortet mit block_height/height/tx_hash/block_hash - oder mit
    None, wenn die Adresse noch nie benutzt wurde (gemessen 16.08.2026, das
    ist KEIN Fehler). Beide Faelle enden hier in None; unterschieden wird
    ueber "erst_bekannt" weiter oben.
    """
    if not isinstance(antwort, dict):
        return None
    hoehe = antwort.get("block_height")
    if hoehe is None:
        hoehe = antwort.get("height")
    if not isinstance(hoehe, int) or hoehe <= 0:
        return None
    return {
        "hoehe": hoehe,
        "txid": antwort.get("tx_hash"),
        "zeit": None,
        "datum": None,
        "alter_s": None,
        "bestaetigungen": (spitze - hoehe + 1) if spitze is not None else None,
    }


# =====================================================================
#  QR-Code
# =====================================================================
# Selbst gerechnet, ohne Bibliothek - das Versprechen "keine Abhaengigkeit,
# kein Aufruf ins Internet" gilt auch fuer ein Bildchen.
#
# Kosten im eigenen Prozess: 2,5 ms fuer eine 29x29-Matrix, 3,3 ms fuer 33x33
# (gemessen 16.08.2026). Der groesste Teil davon sind die acht Masken, die
# gebaut und bewertet werden muessen - das ist die Norm, nicht Verschwendung.
#
# Umfang bewusst klein gehalten: Fassung 1 bis 5, Fehlerkorrektur L,
# Byte-Modus. Das genuegt fuer JEDE Bitcoin-Adresse (Fassung 5 traegt 106
# Bytes, die laengste Adresse hat 62 Zeichen) und spart die eine Stelle, an
# der QR-Erzeuger reihenweise falsch liegen: ab Fassung 6 zerfaellt die
# Nachricht in mehrere Bloecke, die verschraenkt werden muessen. In den
# Fassungen 1-5 mit Stufe L ist es genau EIN Block - kein Verschraenken, kein
# Fehler. Ab Fassung 7 kaeme zusaetzlich die eingebettete Fassungsnummer dazu;
# auch die brauchen wir damit nicht.
#
# ⚠️ NACHGEPRUEFT, nicht geglaubt (16.08.2026). 211 Adressen - elf benannte
# und 200 erzeugte, alle vier Adressarten - wurden zweifach geprueft:
#   * Modul fuer Modul gegen segno 1.6.6, einen fremden Erzeuger: 211 von 211
#     stimmen exakt ueberein, einschliesslich der gewaehlten Maske.
#   * von zxing-cpp 3.1.1, einem fremden LESER, aus dem gerenderten Bild
#     wieder eingelesen: 211 von 211 ergaben genau den Ausgangstext. Auch
#     jede der acht Masken einzeln wurde so gelesen.
# Drei Fehler hat erst dieser Vergleich gefunden, keiner davon sichtbar am
# eigenen Ergebnis: die Reihenfolge der Formatbits (der Code war komplett
# unlesbar), die Bewertung der Masken samt Suchmuster-Regel und das immer
# dunkle Modul. Der Pruefstand steht NICHT im Container - er braucht fremde
# Bibliotheken, und die App verspricht das Gegenteil.

# Gesamtzahl der Codewoerter je Fassung.
QR_GESAMT = {1: 26, 2: 44, 3: 70, 4: 100, 5: 134}
# Fehlerkorrektur-Codewoerter bei Stufe L - je Fassung genau ein Block.
QR_EC_L = {1: 7, 2: 10, 3: 15, 4: 20, 5: 26}
# Mittelpunkte der Ausrichtungsmuster. Aus je zwei Koordinaten werden vier
# Kombinationen, drei davon liegen unter den Suchmustern - bleibt genau eine.
QR_AUSRICHTUNG = {1: (), 2: (6, 18), 3: (6, 22), 4: (6, 26), 5: (6, 30)}
# Stufe L als zwei Bit, wie im Formatfeld verlangt (L=01, M=00, Q=11, H=10).
QR_STUFE_L = 0b01

_EXP, _LOG = None, None


def _gf():
    """Rechentabellen fuer GF(256), Bildungsgesetz 0x11D wie in der Norm."""
    global _EXP, _LOG
    if _EXP is None:
        exp, log, x = [0] * 512, [0] * 256, 1
        for i in range(255):
            exp[i], log[x] = x, i
            x <<= 1
            if x & 0x100:
                x ^= 0x11D
        for i in range(255, 512):
            exp[i] = exp[i - 255]
        _EXP, _LOG = exp, log
    return _EXP, _LOG


def _generator(n):
    """Generatorpolynom vom Grad n, hoechste Potenz zuerst."""
    exp, log = _gf()
    g = [1]
    for i in range(n):
        neu = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            neu[j] ^= c                                   # c * x
            if c:
                neu[j + 1] ^= exp[log[c] + i]             # c * alpha^i
        g = neu
    return g


def _reed_solomon(daten, n):
    """Die n Pruefcodewoerter zu `daten` - Rest der Polynomdivision."""
    exp, log = _gf()
    g = _generator(n)
    rest = list(daten) + [0] * n
    for i in range(len(daten)):
        f = rest[i]
        if f:
            lf = log[f]
            for j, c in enumerate(g):
                if c:
                    rest[i + j] ^= exp[log[c] + lf]
    return rest[len(daten):]


def _bitstrom(daten, fassung):
    """Modus, Laenge, Nutzdaten, Abschluss und Fuellmuster - als Bitliste."""
    datencw = QR_GESAMT[fassung] - QR_EC_L[fassung]
    bits = [0, 1, 0, 0]                                   # Byte-Modus
    # Laengenfeld: in den Fassungen 1-9 sind es acht Bit.
    bits += [(len(daten) >> i) & 1 for i in range(7, -1, -1)]
    for b in daten:
        bits += [(b >> i) & 1 for i in range(7, -1, -1)]
    # Abschluss: bis zu vier Nullen, aber nie ueber die Kapazitaet hinaus.
    bits += [0] * min(4, datencw * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)                        # auf ganze Bytes
    fuell = (0xEC, 0x11)                                  # von der Norm gesetzt
    i = 0
    while len(bits) < datencw * 8:
        bits += [(fuell[i % 2] >> k) & 1 for k in range(7, -1, -1)]
        i += 1
    codewoerter = [int("".join(str(b) for b in bits[k:k + 8]), 2)
                   for k in range(0, len(bits), 8)]
    return codewoerter + _reed_solomon(codewoerter, QR_EC_L[fassung])


def _geruest(fassung):
    """(Matrix, Belegtkarte) mit allen Funktionsmustern - ohne Daten."""
    n = 17 + 4 * fassung
    m = [[0] * n for _ in range(n)]
    fest = [[False] * n for _ in range(n)]

    def setze(r, c, wert):
        if 0 <= r < n and 0 <= c < n:
            m[r][c], fest[r][c] = wert, True

    # Suchmuster samt weisser Trennlinie ringsum.
    for (zr, zc) in ((0, 0), (0, n - 7), (n - 7, 0)):
        for r in range(-1, 8):
            for c in range(-1, 8):
                rand = r in (-1, 7) or c in (-1, 7)
                ring = 0 <= r <= 6 and 0 <= c <= 6 and (
                    r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4))
                setze(zr + r, zc + c, 0 if rand else (1 if ring else 0))

    # Taktmuster: die durchgezogene Linie, an der ein Leser sich ausrichtet.
    for i in range(8, n - 8):
        setze(6, i, 1 - i % 2)
        setze(i, 6, 1 - i % 2)

    # Ausrichtungsmuster - alles ausser den drei Ecken unter den Suchmustern.
    mitten = QR_AUSRICHTUNG[fassung]
    for a in mitten:
        for b in mitten:
            if (a, b) in ((6, 6), (6, n - 7), (n - 7, 6)):
                continue
            for r in range(-2, 3):
                for c in range(-2, 3):
                    setze(a + r, b + c,
                          1 if max(abs(r), abs(c)) != 1 else 0)

    # Platz fuer die Formatangabe freihalten - sie wird zuletzt geschrieben,
    # zusammen mit dem einen Modul, das immer dunkel ist (n-8, 8). Es gehoert
    # zum Formatbereich und darf deshalb bei der Maskenbewertung noch NICHT
    # dunkel sein - sonst faellt die Wahl bei knappen Staenden anders aus.
    for i in range(9):
        fest[8][i] = fest[i][8] = True
    for i in range(8):
        fest[8][n - 1 - i] = fest[n - 1 - i][8] = True
    return m, fest


def _daten_legen(m, fest, codewoerter):
    """Der Zickzack von rechts unten nach links oben, zwei Spalten breit."""
    n = len(m)
    bits = [(c >> i) & 1 for c in codewoerter for i in range(7, -1, -1)]
    i, spalte, aufwaerts = 0, n - 1, True
    while spalte > 0:
        if spalte == 6:               # die Taktspalte wird uebersprungen
            spalte -= 1
        for k in range(n):
            r = (n - 1 - k) if aufwaerts else k
            for c in (spalte, spalte - 1):
                if not fest[r][c]:
                    m[r][c] = bits[i] if i < len(bits) else 0
                    i += 1
        aufwaerts = not aufwaerts
        spalte -= 2


def _maske(nr, r, c):
    """Die acht Maskenformeln der Norm."""
    if nr == 0:
        return (r + c) % 2 == 0
    if nr == 1:
        return r % 2 == 0
    if nr == 2:
        return c % 3 == 0
    if nr == 3:
        return (r + c) % 3 == 0
    if nr == 4:
        return (r // 2 + c // 3) % 2 == 0
    if nr == 5:
        return (r * c) % 2 + (r * c) % 3 == 0
    if nr == 6:
        return ((r * c) % 2 + (r * c) % 3) % 2 == 0
    return ((r + c) % 2 + (r * c) % 3) % 2 == 0


# Das Verhaeltnis 1:1:3:1:1, an dem ein Leser die Suchmuster erkennt. Taucht es
# mitten in den Daten auf, sucht er an der falschen Stelle - deshalb bestraft.
N3_MUSTER = (1, 0, 1, 1, 1, 0, 1)


def _n3(reihe):
    """Wie teuer die suchmuster-aehnlichen Stellen einer Reihe sind.

    Gezaehlt wird nur, wenn neben dem Muster vier helle Module liegen - oder
    der Rand des Symbols, der fuer einen Leser genauso wirkt (so steht es seit
    der Fassung 2015 in Tabelle 11 der Norm; aeltere Anleitungen pruefen ein
    starres Fenster aus elf Modulen und kommen deshalb auf andere Masken).
    """
    n, punkte, i = len(reihe), 0, 0
    while i <= n - 7:
        if tuple(reihe[i:i + 7]) != N3_MUSTER:
            i += 1
            continue
        vorher = reihe[max(i - 4, 0):i]
        nachher = reihe[i + 7:i + 11]
        if i == 0 or i == n - 7 or not any(vorher) or not any(nachher):
            punkte += 40
            i += 7
        else:
            # Kein Treffer: das mittlere Dunkelfeld kann der Anfang des
            # naechsten Musters sein, also nur bis dorthin weiterruecken.
            i += 4
    return punkte


def _strafe(m):
    """Die vier Strafregeln der Norm - je kleiner, desto besser liest ein Leser."""
    n, summe = len(m), 0
    reihen = [list(z) for z in m] + [list(s) for s in zip(*m)]

    # 1: lange Laeufe gleicher Farbe in Zeile und Spalte
    for reihe in reihen:
        lauf, letzte = 1, reihe[0]
        for wert in reihe[1:]:
            if wert == letzte:
                lauf += 1
            else:
                if lauf >= 5:
                    summe += 3 + lauf - 5
                lauf, letzte = 1, wert
        if lauf >= 5:
            summe += 3 + lauf - 5

    # 2: gleichfarbige Zweier-Bloecke
    for r in range(n - 1):
        for c in range(n - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                summe += 3

    # 3: das Muster, das einem Suchmuster gleicht
    for reihe in reihen:
        summe += _n3(reihe)

    # 4: Abweichung vom halb-halb-Verhaeltnis, in Fuenferschritten gemessen
    dunkel = sum(sum(z) for z in m)
    summe += 10 * int(abs(dunkel * 100.0 / (n * n) - 50) / 5)
    return summe


def _formatbits(maske):
    """15 Bit Formatangabe: Stufe, Maske, BCH-Pruefung, fester XOR."""
    daten = (QR_STUFE_L << 3) | maske
    rest = daten << 10
    for i in range(4, -1, -1):
        if rest & (1 << (i + 10)):
            rest ^= 0x537 << i
    return ((daten << 10) | rest) ^ 0x5412


def _format_schreiben(m, maske):
    """Die 15 Formatbits an ihre zwei Plaetze - hoechstwertiges Bit zuerst.

    ⚠️ Genau hier lag der einzige Fehler des ersten Entwurfs: er legte das
    NIEDERSTWERTIGE Bit zuerst. Der Datenteil des Codes war dann Modul fuer
    Modul richtig, aber kein Leser der Welt kam hinein - der Formatbereich
    sagt ihm ja erst, welche Maske er abziehen muss. Aufgefallen ist es nur,
    weil ein fremder Leser danebenstand.
    """
    n, f = len(m), _formatbits(maske)

    def bit(i):
        return (f >> (14 - i)) & 1

    for i in range(6):
        m[8][i] = bit(i)
    m[8][7], m[8][8], m[7][8] = bit(6), bit(7), bit(8)
    for i in range(9, 15):
        m[14 - i][8] = bit(i)
    for i in range(7):
        m[n - 1 - i][8] = bit(i)
    for i in range(7, 15):
        m[8][n - 15 + i] = bit(i)
    m[n - 8][8] = 1                      # das immer dunkle Modul


def qr_matrix(text):
    """Die fertige Modulmatrix als Liste von 0/1-Zeilen. None, wenn es nicht passt."""
    if not text:
        return None
    daten = text.encode("ascii", "ignore")
    fassung = None
    for v in sorted(QR_GESAMT):
        # 4 Bit Modus + 8 Bit Laenge + Nutzdaten muessen hineinpassen.
        if 4 + 8 + 8 * len(daten) <= 8 * (QR_GESAMT[v] - QR_EC_L[v]):
            fassung = v
            break
    if fassung is None:
        return None

    codewoerter = _bitstrom(daten, fassung)
    grund, fest = _geruest(fassung)
    _daten_legen(grund, fest, codewoerter)

    # Alle acht Masken bauen und die mit der kleinsten Strafe nehmen. Das ist
    # keine Kosmetik: eine schlechte Maske erzeugt Flaechen und Muster, an
    # denen ein Leser haengenbleibt.
    #
    # ⚠️ Bewertet wird OHNE die Formatangabe (Norm 7.8). Wer sie vorher
    # hineinschreibt, bewertet 31 Module mit, die von der Maske gar nicht
    # beruehrt werden - und waehlt bei knappen Staenden eine andere Maske als
    # jeder normkonforme Erzeuger. Lesbar waeren beide; gleich sind sie nicht.
    beste, beste_strafe = None, None
    for nr in range(8):
        m = [[grund[r][c] ^ (1 if (not fest[r][c] and _maske(nr, r, c)) else 0)
              for c in range(len(grund))] for r in range(len(grund))]
        s = _strafe(m)
        if beste_strafe is None or s < beste_strafe:
            beste, beste_strafe, beste_nr = m, s, nr
    _format_schreiben(beste, beste_nr)
    return beste


def qr_svg_daten(text, rand=4):
    """Was die Vorlage fuer ein <svg> braucht: Kantenlaenge und ein Pfad.

    Ein einziger Pfad statt tausend <rect>: waagerecht zusammenhaengende
    Module werden zu einem Strich zusammengefasst, das halbiert die Ausgabe.
    `rand` ist die Ruhezone - vier Module sind das Mindestmass der Norm,
    ohne sie findet mancher Leser den Code gar nicht erst.
    """
    m = qr_matrix(text)
    if not m:
        return None
    stuecke = []
    for r, zeile in enumerate(m):
        c = 0
        while c < len(zeile):
            if not zeile[c]:
                c += 1
                continue
            start = c
            while c < len(zeile) and zeile[c]:
                c += 1
            stuecke.append("M%d %dh%dv1h-%dz"
                           % (start + rand, r + rand, c - start, c - start))
    return {"groesse": len(m), "rand": rand, "gesamt": len(m) + 2 * rand,
            "pfad": "".join(stuecke)}
