"""Miner und Blockraum: wer baut die Bloecke, und wie fuellen sie sie?

Die Seite /mining beantwortet vier Fragen ueber die letzten 24 Bloecke:
wer sie gebaut hat, wie voll sie waren, was der Bau eingebracht hat und in
welchem Takt sie kamen. Alles davon ist eine Beobachtung am eigenen Knoten -
geschaetzt wird nichts, und was eine Quelle nicht hergibt, bleibt ein Strich.

⚠️ DER COINBASE-WEG - warum er NICHT ueber bitcoind laeuft
Der Erzeuger schreibt seinen Namen in die Coinbase-Transaktion. Um sie zu lesen,
braucht man ihre Txid - und die steht nur in `getblock` Stufe 1, das in
rpc.BILLIG ausdruecklich fehlt (es liefert die Txid-Liste des ganzen Blocks,
bei 4.000 Transaktionen einige hundert Kilobyte JSON je Block, hier mal 24).
getblockstats liefert keine Txids, der Kopf nur die Merkle-Wurzel, und die ist
ein Hash - aus ihr folgt nichts zurueck.

Der billige Weg fuehrt deshalb am Knoten VORBEI, ueber den Electrum-Index, der
fuer die Adressansicht ohnehin schon laeuft:

    blockchain.transaction.id_from_pos(hoehe, 0, false)   Txid an Position 0
    blockchain.transaction.get(txid, false)               rohe Transaktion

An Fulcrum 2.1.1 auf .67 gemessen (16.08.2026): **0,2-0,5 ms** und
**0,6-0,9 ms** je Aufruf, sechs Bloecke komplett in 6 ms - ueber EINE
Verbindung, nacheinander. Vierundzwanzig Bloecke kosten damit rund 25 ms und
den Bitcoin-Knoten KEINEN einzigen zusaetzlichen Aufruf. Das Rohformat zerlegen
wir selbst (_coinbase_skript); eine Bibliothek braucht es dafuer nicht.

Gegenprobe gegen Verwechslung: seit BIP 34 MUSS die Coinbase mit der eigenen
Blockhoehe beginnen. Stimmt sie nicht mit der erwarteten ueberein, verwerfen wir
den Fund ganz - lieber ein Strich als ein Name am falschen Block. Damit kann
auch ein Index, der gerade einen anderen Zweig sieht, hier nichts anrichten.

Fehlt der Electrum-Server, fehlt genau die Miner-Spalte. Alles Uebrige steht.

KOSTEN am Bitcoin-Knoten (gemessen 15.08.2026, siehe rpc.py):
    getblockchaininfo    9 ms   einmal: Kettenspitze
    getblockstats    19-30 ms   x24 fuer das Fenster
    getblockstats    19-30 ms   x1  fuer den Vorlaeufer (nur `time`)
Nacheinander waeren das gut 700 ms; das Tor laesst vier Aufrufe gleichzeitig
durch, also sieben Runden - die Seite steht nach ungefaehr 200 ms. Mehr als 24
Bloecke holen wir nicht: das sind rund vier Stunden Kette, genug fuer eine
Verteilung, und wenig genug fuer einen Knoten, auf dem echtes Geld liegt.

Selbstpruefung ohne Knoten und ohne Netz:  python3 -m satscope.mining
"""
import asyncio
import time

from . import elektrum
from .rpc import RpcFehler
from .sprache import STANDARD

# Wie viele Bloecke die Seite betrachtet. 24 sind rund vier Stunden Kette.
FENSTER = 24

# Konsensgrenze: 4.000.000 Gewichtseinheiten. Nicht die 1 MB, die alle zitieren -
# die Fuellung an der Groesse zu messen waere seit SegWit falsch.
GEWICHTSGRENZE = 4000000

# Zielabstand zwischen zwei Bloecken (Sekunden).
ZIELABSTAND = 600

# Ab dieser Fuellung gilt ein Block als randvoll.
RANDVOLL = 99.0

# Unter so vielen Transaktionen faellt ein Block auf. Ein Block mit weniger als
# zehn hat fast nichts eingebaut - meist, weil der Miner ohne vollstaendige
# Mempool-Sicht baute (leerer Block direkt nach einem Fund) und lieber sofort
# rechnet als auf die Blockdaten zu warten.
AUFFAELLIG_AB = 10

# Alle 210.000 Bloecke halbiert sich die Belohnung.
EPOCHENLAENGE = 210000

# Nur diese Felder holen wir. Das ist keine Kosmetik: bitcoind rechnet nur,
# wonach gefragt wird - jedes weggelassene Feld ist gesparte Knotenzeit.
STATSFELDER = ["height", "time", "total_size", "total_weight", "txs",
               "totalfee", "subsidy", "swtxs", "utxo_increase"]

# Der Vorlaeufer liefert nur den Zeitstempel: ohne ihn haette der aelteste
# Block des Fensters keinen Abstand, mit ihm haben alle 24 einen.
VORLAEUFERFELDER = ["time"]

# Hoechstens so viele Coinbase-Marken bleiben gemerkt. Bloecke aendern sich
# nicht mehr, ihre Coinbase also auch nicht - der Eintrag gilt fuer immer.
MERKGRENZE = 512

# Wie sich Pools selbst nennen. Gesucht wird als Zeichenfolge im Coinbase-Skript,
# kleingeschrieben. Die Liste ist bewusst KNAPP und auf Eindeutigkeit getrimmt:
# was hier nicht steht, wird nicht geraten, sondern als Rohtext gezeigt - so
# steht am Ende nie ein falscher Name an einem Block. Reihenfolge = Vorrang.
#
# ⚠️ KEINE fuehrenden Schraegstriche fordern. Der erste Entwurf tat das
# ("/spiderpool/") und uebersah am echten Knoten vier von 24 Bloecken: die
# Marken lauten dort "jSpiderPool/609/", "binance/}", "Powered by Luxor Tech"
# und "j| MARA Made in USA" - der Pool steht mitten im Text, nicht am Anfang.
# Gemessen an den Bloecken 962715-962738 (16.08.2026).
POOL_MARKEN = (
    ("foundry usa", "Foundry USA"),
    ("f2pool", "F2Pool"),
    ("viabtc", "ViaBTC"),
    ("antpool", "AntPool"),
    ("btc.com", "BTC.com"),
    ("binance", "Binance Pool"),
    ("braiins", "Braiins Pool"),
    ("/slush/", "Braiins Pool"),       # "slush" allein waere zu gewoehnlich
    ("spiderpool", "SpiderPool"),
    ("marapool", "MARA Pool"),
    ("mara pool", "MARA Pool"),
    ("mara made in usa", "MARA Pool"),
    ("luxor", "Luxor"),
    ("sbicrypto", "SBI Crypto"),
    ("secpool", "SECPOOL"),
    ("ultimuspool", "ULTIMUSPOOL"),
    ("poolin", "Poolin"),
    ("emcd", "EMCD"),
    ("whitepool", "WhitePool"),
    ("ocean.xyz", "OCEAN"),
    ("bitfufu", "BitFuFu"),
    ("carbonnegative", "Carbon Negative"),
    ("terrapool", "Terra Pool"),
    ("pega pool", "PEGA Pool"),
    ("nicehash", "NiceHash"),
    ("kano.is", "KanoPool"),
    ("bitfarms", "Bitfarms"),
    ("/btcc/", "BTCC"),                # vier Buchstaben - nur mit Klammern
    ("1thash", "1THash"),
    ("rawpool", "Rawpool"),
    ("sigmapool", "SigmaPool"),
    ("mining-dutch", "Mining-Dutch"),
    ("public-pool.io", "Public Pool"),
    ("solo.ckpool.org", "Solo CKPool"),
    ("ckpool", "CKPool"),
)

# Txid -> {"text": ..., "name": ...}. Siehe MERKGRENZE.
_MARKEN_CACHE = {}


# ------------------------------------------------------------------ Werkzeug
async def _sicher(tor, methode, *argumente):
    """Ruft auf und liefert None statt zu werfen - wie knoten._sicher().

    Bewusst hier noch einmal geschrieben statt importiert: der Strich statt
    einer erfundenen Zahl ist die wichtigste Regel dieses Projekts, und sie
    soll in jedem Modul sichtbar dastehen, das sie anwendet.
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


def _median(werte):
    """Median. Der Mittelwert waere hier falsch: ein einziger 40-Minuten-Block
    zieht ihn ueber den ganzen Tag, obwohl 23 andere puenktlich waren."""
    if not werte:
        return None
    s = sorted(werte)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def btc(sat, stellen=3, sprache=STANDARD):
    """Satoshi als BTC-Text, sprachrichtig - und ganzzahlig gerechnet.

    Fliesskomma hat bei Geld nichts zu suchen: 0,1 + 0,2 ist dort nicht 0,3,
    und bei Betraegen faellt so etwas irgendwann auf. Deshalb wird auf die
    gewuenschte Stelle ganzzahlig gerundet und erst danach getrennt.
    Drei Nachkommastellen genuegen hier: es geht um Blockbelohnungen, nicht um
    einzelne Satoshi - und "3,125" liest sich, "3.12500000" nicht.
    """
    if sat is None:
        return "–"
    stellen = max(0, min(8, int(stellen)))
    teiler = 10 ** (8 - stellen)
    zeichen = "-" if int(sat) < 0 else ""
    # Kaufmaennisch runden, bevor getrennt wird - sonst waeren 0,9999 BTC
    # abgeschnitten zu "0,999" und der Fehler haette immer dasselbe Vorzeichen.
    stufen = (abs(int(sat)) + teiler // 2) // teiler
    ganz, rest = divmod(stufen, 10 ** stellen) if stellen else (stufen, 0)
    gruppiert = "{:,}".format(ganz)                   # englische Schreibweise
    if not stellen:
        return zeichen + (gruppiert.replace(",", ".") if sprache == "de" else gruppiert)
    if sprache == "de":
        return "%s%s,%0*d" % (zeichen, gruppiert.replace(",", "."), stellen, rest)
    return "%s%s.%0*d" % (zeichen, gruppiert, stellen, rest)


# ------------------------------------------------------- Coinbase zerlegen
def _varint(d, p):
    """(Wert, neue Stelle) einer Bitcoin-Zahl veraenderlicher Laenge."""
    if p >= len(d):
        return None, p
    x = d[p]
    p += 1
    if x < 0xFD:
        return x, p
    breite = {0xFD: 2, 0xFE: 4, 0xFF: 8}[x]
    if p + breite > len(d):
        return None, p
    return int.from_bytes(d[p:p + breite], "little"), p + breite


def _coinbase_skript(roh_hex):
    """Das scriptSig des ersten Eingangs aus einer rohen Transaktion.

    Nur der Anfang wird gelesen - Ausgaenge und Zeugen interessieren nicht.
    Sieht die Transaktion nicht wie eine Coinbase aus (ein Eingang, Vorgaenger
    lauter Nullen, Index 0xFFFFFFFF), liefern wir None und sagen gar nichts:
    ein falsch gelesener Eingang waere schlimmer als eine leere Spalte.
    """
    try:
        d = bytes.fromhex(roh_hex or "")
    except (ValueError, TypeError):
        return None
    if len(d) < 42:
        return None
    p = 4                                       # Fassungsnummer
    # SegWit-Markierung 0x00 0x01 - jede Coinbase seit SegWit traegt sie.
    if d[p] == 0x00 and d[p + 1] == 0x01:
        p += 2
    anzahl, p = _varint(d, p)
    if anzahl != 1 or p + 36 > len(d):
        return None
    vorgaenger, index = d[p:p + 32], int.from_bytes(d[p + 32:p + 36], "little")
    p += 36
    if vorgaenger != b"\x00" * 32 or index != 0xFFFFFFFF:
        return None
    laenge, p = _varint(d, p)
    # Konsensregel: das Coinbase-Skript ist 2 bis 100 Byte lang. Was daneben
    # liegt, haben wir falsch gelesen - dann lieber nichts behaupten.
    if laenge is None or not 2 <= laenge <= 100 or p + laenge > len(d):
        return None
    return d[p:p + laenge]


def bip34_hoehe(skript):
    """Die Blockhoehe, die der Miner selbst voranstellen MUSS (BIP 34).

    Sie ist unsere Gegenprobe: passt sie nicht zu der Hoehe, nach der wir
    gefragt haben, gehoert die Transaktion zu einem anderen Block - etwa weil
    der Index gerade einen konkurrierenden Zweig sieht. Dann wird der Fund
    verworfen. Ein Name am falschen Block waere genau die Sorte stiller Fehler,
    die niemand nachprueft.
    """
    if not skript:
        return None
    n = skript[0]
    if not 1 <= n <= 4 or len(skript) < 1 + n:
        return None
    return int.from_bytes(skript[1:1 + n], "little")


def lesbarer_lauf(roh, mindest=3, grenze=40):
    """Laengster druckbarer ASCII-Lauf in einem Haufen Bytes.

    Die Coinbase traegt Text zwischen Binaerdaten (Hoehe, Zaehler, Wurzeln der
    Zusatzdaten). Der laengste zusammenhaengende Lauf trifft die Kennung
    zuverlaessiger als jede Quote ueber das Ganze.
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


def pool_name(skript):
    """Der Pool, der sich selbst so nennt - oder None.

    latin-1 dekodiert JEDES Byte ohne Ausnahme; wir suchen nur Zeichenfolgen,
    eine Zeichensatzfrage stellt sich dabei nicht.
    """
    if not skript:
        return None
    text = skript.decode("latin-1", "ignore").lower()
    for marke, name in POOL_MARKEN:
        if marke in text:
            return name
    return None


def _merken(txid, marke):
    """Kleiner Vorrat gelesener Coinbase-Marken. Bloecke aendern sich nicht."""
    if len(_MARKEN_CACHE) >= MERKGRENZE:
        # Aeltester Eintrag zuerst - dict behaelt seit 3.7 die Einfuegereihenfolge.
        for alt in list(_MARKEN_CACHE)[:MERKGRENZE // 4]:
            _MARKEN_CACHE.pop(alt, None)
    _MARKEN_CACHE[txid] = marke


async def coinbase_marken(hoehen, zeitlimit=2.0, gesamtzeit=4.0):
    """{hoehe: {"text":..., "name":...}} - ueber den Electrum-Index.

    Eine Verbindung, alle Bloecke nacheinander: gemessen rund 1,1 ms je Block
    (Fulcrum 2.1.1, .67, 16.08.2026), 24 Bloecke in 36 ms. Vierundzwanzig
    parallele Verbindungen waeren schneller und unhoeflicher; bei 36 ms lohnt
    das nicht.

    ZWEI Bremsen, beide noetig: `zeitlimit` deckelt die EINZELNE Antwort,
    `gesamtzeit` die ganze Runde. Nur mit der ersten koennte ein zaeher Index
    die Seite 24 mal warten lassen; nur mit der zweiten haenge eine einzige
    Antwort ewig. Ist die Zeit auf, bleibt stehen, was schon beantwortet ist -
    der Rest fehlt und zeigt einen Strich.
    """
    host, port = elektrum.ziel()
    if not host or not port or not hoehen:
        return {}
    raus = {}
    schluss = time.monotonic() + gesamtzeit
    try:
        async with elektrum.Verbindung(host, port, zeitlimit) as v:
            for h in hoehen:
                if time.monotonic() > schluss:
                    break
                txid = await v.frage("blockchain.transaction.id_from_pos",
                                     [h, 0, False])
                if not isinstance(txid, str) or len(txid) != 64:
                    continue
                marke = _MARKEN_CACHE.get(txid)
                if marke is None:
                    roh = await v.frage("blockchain.transaction.get",
                                        [txid, False])
                    skript = _coinbase_skript(roh)
                    # Die Gegenprobe aus BIP 34. Sie ist der Grund, warum hier
                    # nie ein Name am falschen Block landen kann.
                    if skript is None or bip34_hoehe(skript) != h:
                        continue
                    marke = {"text": lesbarer_lauf(skript),
                             "name": pool_name(skript)}
                    _merken(txid, marke)
                raus[h] = marke
    except (OSError, asyncio.TimeoutError, ValueError, TypeError,
            elektrum.ElektrumFehler):
        return raus
    return raus


# ------------------------------------------------------------- Auswertung
def _saeule(wert, massstab, mindest=2.0):
    """Balkenhoehe in Prozent. Der Mindestwert ist Absicht und dokumentiert:

    ein Block mit 0,02 % Fuellung waere sonst UNSICHTBAR, und gerade er ist der
    interessante Fall. Die Zahl daneben bleibt die echte - verzerrt wird nur
    das Bild, und nur nach oben, nur bis zur Sichtbarkeitsgrenze.
    """
    if wert is None or not massstab:
        return None
    return max(mindest, min(100.0, abs(wert) / massstab * 100.0))


def auswerten(stats, marken=None, vorlaeufer_zeit=None, spitze=None, jetzt=None,
              hoehen=None):
    """Rohe getblockstats zu einer fertigen Seite verdichten.

    Reine Rechnung, kein Netz - damit genau dieser Teil ohne Knoten pruefbar
    ist. `stats` kommt aufsteigend nach Hoehe; ein ausgefallener Aufruf steht
    als None drin und behaelt seinen Platz, damit die Balkenreihe nicht
    zusammenrutscht und ein fehlender Block als Luecke sichtbar bleibt.

    `hoehen` sind die Hoehen, nach denen gefragt wurde. Sie mitzugeben ist kein
    Luxus: fiel getblockstats aus, steht die Hoehe in keiner Antwort mehr - die
    Luecke waere dann nicht einmal anklickbar, und ihr Miner bliebe ungenannt,
    obwohl der Index ihn kennt.
    """
    marken = marken or {}
    jetzt = int(time.time()) if jetzt is None else int(jetzt)
    hoehen = list(hoehen or [])

    bloecke = []
    vorzeit = vorlaeufer_zeit
    for i, s in enumerate(stats):
        s = s if isinstance(s, dict) else None
        hoehe = (s or {}).get("height")
        if hoehe is None and i < len(hoehen):
            hoehe = hoehen[i]
        zeit = (s or {}).get("time")
        gewicht = (s or {}).get("total_weight")
        txs = (s or {}).get("txs")
        subsidy = (s or {}).get("subsidy")
        gebuehren = (s or {}).get("totalfee")
        lohn = (subsidy + gebuehren) if (subsidy is not None
                                         and gebuehren is not None) else None
        # Abstand zum Vorgaenger. NEGATIV ist erlaubt und wird nicht versteckt:
        # ein Zeitstempel muss nur ueber dem Median der elf Vorgaenger liegen,
        # nicht ueber dem des direkten Vorgaengers. Miner-Uhren weichen ab.
        abstand = (zeit - vorzeit) if (zeit is not None and vorzeit is not None) else None
        # ⚠️ Auch wenn dieser Block keine Zeit hat, ruckt der Merker weiter.
        # Der erste Entwurf behielt die letzte bekannte Zeit - dann bekam der
        # Block NACH einer Luecke die Summe zweier Abstaende als seinen eigenen
        # und sah wie ein ungewoehnlich langsamer Block aus. Ueber eine Luecke
        # hinweg wissen wir den Abstand nicht; dann steht dort ein Strich.
        vorzeit = zeit

        marke = marken.get(hoehe) if hoehe is not None else None
        bloecke.append({
            "hoehe": hoehe,
            "stats_da": s is not None,
            "zeit": zeit,
            "alter": (jetzt - zeit) if zeit is not None else None,
            "gewicht": gewicht,
            "groesse": (s or {}).get("total_size"),
            "fuellung": _anteil(gewicht, GEWICHTSGRENZE),
            "txs": txs,
            # txs zaehlt die Coinbase mit. Ein Block "mit einer Transaktion"
            # enthaelt also keine einzige Zahlung.
            "zahlungen": (txs - 1) if txs else None,
            "leer": txs is not None and txs <= 1,
            "auffaellig": txs is not None and txs < AUFFAELLIG_AB,
            "subsidy_sat": subsidy,
            "gebuehren_sat": gebuehren,
            "lohn_sat": lohn,
            "gebuehren_anteil": _anteil(gebuehren, lohn),
            "segwit_anteil": _anteil((s or {}).get("swtxs"), txs),
            "utxo": (s or {}).get("utxo_increase"),
            "abstand": abstand,
            "abstand_minuten": (abs(abstand) / 60.0) if abstand is not None else None,
            "rueckwaerts": abstand is not None and abstand < 0,
            "miner": {
                "name": (marke or {}).get("name"),
                "text": (marke or {}).get("text"),
                "erkannt": bool((marke or {}).get("name")),
            } if marke else None,
        })

    da = [b for b in bloecke if b["stats_da"]]

    # ---- Balkenhoehen. Die Fuellung hat ihren eigenen, festen Massstab: die
    # Konsensgrenze. Sie auf den vollsten Block des Fensters zu skalieren waere
    # bequemer und falsch - ein Fenster aus lauter halbvollen Bloecken saehe
    # dann aus wie ein Fenster aus lauter vollen.
    for b in bloecke:
        b["fuellung_saeule"] = _saeule(b["fuellung"], 100.0)

    lohn_max = max((b["lohn_sat"] for b in da if b["lohn_sat"] is not None),
                   default=None)
    for b in bloecke:
        b["lohn_saeule"] = _saeule(b["lohn_sat"], lohn_max)
        # Anteil der Gebuehren INNERHALB der Saeule. Bei heute ueblichen ein
        # bis drei Prozent ist das ein Streifen von wenigen Pixeln - genau das
        # ist die Aussage. Die Mindesthoehe haelt ihn sichtbar, ohne ihn zu
        # vergroessern: die Prozentzahl daneben bleibt die echte.
        b["gebuehren_saeule"] = _saeule(b["gebuehren_anteil"], 100.0, mindest=2.0)

    abstaende = [b["abstand"] for b in da if b["abstand"] is not None]
    oben = max([a for a in abstaende if a > 0] or [0])
    unten = max([-a for a in abstaende if a < 0] or [0])
    # EIN Massstab fuer beide Richtungen. Die Saeule misst sich zwar an ihrer
    # eigenen Haelfte (nach oben an `oben`, nach unten an `unten`), aber die
    # Haelften selbst teilen sich das Bild im Verhaeltnis oben:unten - damit
    # ist ein Pixel oberhalb der Nulllinie genauso viel Zeit wie einer
    # darunter. Zwei getrennte Massstaebe waeren bequemer und wuerden einen
    # Ruecklauf von zwei Minuten so gross zeichnen wie einen Abstand von
    # vierzig; das waere gelogen.
    spanne = oben + unten
    for b in bloecke:
        a = b["abstand"]
        if a is None or not spanne:
            b["abstand_saeule"] = None
        else:
            b["abstand_saeule"] = _saeule(a, oben if a >= 0 else unten)

    fuellungen = [b["fuellung"] for b in da if b["fuellung"] is not None]
    gebuehren_summe = sum(b["gebuehren_sat"] for b in da
                          if b["gebuehren_sat"] is not None) if da else None
    subsidy_summe = sum(b["subsidy_sat"] for b in da
                        if b["subsidy_sat"] is not None) if da else None
    hat_geld = any(b["gebuehren_sat"] is not None for b in da) and \
        any(b["subsidy_sat"] is not None for b in da)
    lohn_summe = (subsidy_summe + gebuehren_summe) if hat_geld else None

    # Was dieselben Gebuehren nach der naechsten Halbierung bedeuten wuerden.
    # Reine Arithmetik auf den eigenen Zahlen des Fensters, keine Prognose.
    nach_halbierung = None
    if hat_geld and (subsidy_summe // 2 + gebuehren_summe):
        nach_halbierung = _anteil(gebuehren_summe,
                                  subsidy_summe // 2 + gebuehren_summe)

    hoehen = [b["hoehe"] for b in da if b["hoehe"] is not None]
    zeiten = [b["zeit"] for b in da if b["zeit"] is not None]
    epoche = (spitze // EPOCHENLAENGE) if spitze is not None else None
    halbierung = ((epoche + 1) * EPOCHENLAENGE) if epoche is not None else None
    bis_halbierung = (halbierung - spitze) if halbierung is not None else None

    return {
        "erreichbar": bool(da),
        "spitze": spitze,
        "bloecke": bloecke,
        # Zwei verschiedene Zahlen, und der Unterschied ist wichtig:
        # `blockzahl` ist das betrachtete Fenster (so viele Saeulen stehen im
        # Bild), `fenster` sind die Bloecke, zu denen der Knoten wirklich
        # Zahlen geliefert hat. Ueberschriften nennen die erste, Mittelwerte
        # rechnen mit der zweiten.
        "blockzahl": len(bloecke),
        "fenster": len(da),
        "von_hoehe": min(hoehen) if hoehen else None,
        "bis_hoehe": max(hoehen) if hoehen else None,
        # Gemessen, nicht gerechnet: bei Zehn-Minuten-Annahme waeren 24 Bloecke
        # immer vier Stunden - in Wirklichkeit selten.
        "fenster_stunden": ((max(zeiten) - min(zeiten)) / 3600.0
                            if len(zeiten) > 1 else None),

        # ---- Blockraum
        "fuellung_mittel": (sum(fuellungen) / len(fuellungen)) if fuellungen else None,
        "randvoll": sum(1 for f in fuellungen if f >= RANDVOLL),
        "auffaellige": [b["hoehe"] for b in da if b["auffaellig"]],
        "leere": [b["hoehe"] for b in da if b["leer"]],
        "grenze_auffaellig": AUFFAELLIG_AB,
        "txs_summe": sum(b["txs"] for b in da if b["txs"] is not None) or None,

        # ---- Geld
        "gebuehren_sat": gebuehren_summe if hat_geld else None,
        "subsidy_sat": subsidy_summe if hat_geld else None,
        "lohn_sat": lohn_summe,
        "gebuehren_anteil": _anteil(gebuehren_summe, lohn_summe) if hat_geld else None,
        "anteil_nach_halbierung": nach_halbierung,
        "epoche": epoche,
        "halbierung": halbierung,
        "bis_halbierung": bis_halbierung,
        # Tage bei ZIELABSTAND je Block - ausdruecklich eine Annahme, keine
        # Messung; der Satz im Katalog nennt sie deshalb mit.
        "tage_halbierung": (bis_halbierung * ZIELABSTAND / 86400.0)
                           if bis_halbierung else None,

        # ---- Takt
        "abstaende_n": len(abstaende),
        "abstand_median": _median(abstaende),
        "abstand_min": min(abstaende) if abstaende else None,
        "abstand_max": max(abstaende) if abstaende else None,
        "rueckwaerts": sum(1 for a in abstaende if a < 0),
        # Wo die Nulllinie im Bild liegt: ohne Ruecklaeufer ganz unten, sonst
        # so weit oben, wie der tiefste Ruecklaeufer Platz braucht.
        "takt_oben": _anteil(oben, spanne) if spanne else 100.0,
        # Die Zehn-Minuten-Marke, gemessen an der Nulllinie nach oben. Passt sie
        # nicht ins Bild (kein Abstand erreicht sie), zeichnen wir sie nicht.
        "ziel_marke": _anteil(ZIELABSTAND, oben) if oben >= ZIELABSTAND else None,
        "ziel": ZIELABSTAND,

        # ---- Miner
        "miner": _miner_verteilung(da),
        "miner_da": any(b["miner"] for b in da),
        "miner_erkannt": sum(1 for b in da if b["miner"] and b["miner"]["erkannt"]),

        "btc": lambda sat, stellen=3: btc(sat, stellen),
    }


def _miner_verteilung(bloecke):
    """Wer wie viele der Bloecke gebaut hat, mit seiner mittleren Fuellung.

    Gruppiert wird NUR nach erkanntem Namen. Alles Uebrige landet in einer
    einzigen Gruppe "nennt sich nicht": nach dem Rohtext zu gruppieren waere
    verlockend, aber viele Marken tragen wechselnde Anhaengsel
    ("/ViaBTC/Mined by asicfoxfee/") - daraus wuerden lauter Einzelgruppen, die
    eine Verteilung vortaeuschen, wo keine gemessen wurde. Der Rohtext steht
    dafuer in der Tabelle bei jedem einzelnen Block.
    """
    gruppen = {}
    for b in bloecke:
        if not b["miner"]:
            continue                       # kein Index, keine Aussage
        name = b["miner"]["name"]
        g = gruppen.setdefault(name, {"name": name, "erkannt": bool(name),
                                      "anzahl": 0, "fuellungen": [], "leer": 0})
        g["anzahl"] += 1
        if b["fuellung"] is not None:
            g["fuellungen"].append(b["fuellung"])
        if b["auffaellig"]:
            g["leer"] += 1

    gesamt = sum(g["anzahl"] for g in gruppen.values())
    if not gesamt:
        return []
    hoechste = max(g["anzahl"] for g in gruppen.values())
    raus = []
    for g in gruppen.values():
        f = g["fuellungen"]
        raus.append({
            "name": g["name"],
            "erkannt": g["erkannt"],
            "anzahl": g["anzahl"],
            "anteil": _anteil(g["anzahl"], gesamt),
            # Der Balken misst sich am haeufigsten Bauer, nicht am ganzen
            # Fenster: bei sieben Pools waere sonst jeder Balken ein Stummel.
            "balken": _saeule(g["anzahl"], hoechste),
            "fuellung_mittel": (sum(f) / len(f)) if f else None,
            "leer": g["leer"],
        })
    # Haeufigster zuerst; die namenlose Gruppe faellt ans Ende, weil sie keine
    # Aussage ueber einen Bauer ist, sondern der Rest.
    raus.sort(key=lambda g: (not g["erkannt"], -g["anzahl"], g["name"] or ""))
    return raus


# ---------------------------------------------------------------- die Seite
async def uebersicht(tor, sprache=STANDARD, blockzahl=FENSTER, jetzt=None):
    """Alles, was mining.html braucht - jeder Wert einzeln abgesichert.

    Faellt ein Aufruf aus, fehlt genau seine Zahl; faellt der Electrum-Index
    aus, fehlt genau die Miner-Spalte. Geleert wird die Seite nie.
    """
    kette = await _sicher(tor, "getblockchaininfo")
    spitze = (kette or {}).get("blocks")
    if spitze is None:
        d = auswerten([], spitze=None, jetzt=jetzt)
        d["btc"] = lambda sat, stellen=3: btc(sat, stellen, sprache)
        return d

    hoehen = [h for h in range(spitze - blockzahl + 1, spitze + 1) if h >= 0]
    vorlaeufer = hoehen[0] - 1 if hoehen and hoehen[0] > 0 else None

    # Alles nebenlaeufig. Das Tor deckelt selbst auf vier gleichzeitige
    # Aufrufe; die Coinbase-Marken laufen ueber den Electrum-Index und belasten
    # den Bitcoin-Knoten gar nicht - sie warten also nicht auf ihn.
    stats, vor, marken = await asyncio.gather(
        asyncio.gather(*[_sicher(tor, "getblockstats", h, STATSFELDER)
                         for h in hoehen]),
        _sicher(tor, "getblockstats", vorlaeufer, VORLAEUFERFELDER)
        if vorlaeufer is not None else _nichts(),
        coinbase_marken(hoehen),
    )

    d = auswerten(list(stats), marken, (vor or {}).get("time"), spitze, jetzt,
                  hoehen)
    # Die Sprache kennt erst die Anfrage, nicht das Modul - deshalb wird der
    # BTC-Formatierer hier ersetzt statt in auswerten() gebaut.
    d["btc"] = lambda sat, stellen=3: btc(sat, stellen, sprache)
    d["erreichbar"] = True
    return d


async def _nichts():
    """Platzhalter, damit asyncio.gather() eine feste Stellenzahl behaelt."""
    return None


# ------------------------------------------------------- Selbstpruefung
def _selbsttest():
    """Prueft gegen ERFUNDENE Daten - kein Knoten, kein Netz.

    Die wichtigsten Faelle zuerst: dass ein Ruecklauf sichtbar bleibt und dass
    eine Coinbase am falschen Block verworfen wird.
    """
    fehler = []

    def pruefe(name, ist, soll):
        if ist != soll:
            fehler.append("%s: %r erwartet, %r bekommen" % (name, soll, ist))
            print("  FEHLER  %s" % name)
        else:
            print("  ok      %s" % name)

    def stat(hoehe, zeit, gewicht=3900000, txs=3000, gebuehren=4000000):
        return {"height": hoehe, "time": zeit, "total_weight": gewicht,
                "total_size": gewicht // 4, "txs": txs, "totalfee": gebuehren,
                "subsidy": 312500000, "swtxs": txs // 2, "utxo_increase": 100}

    print("Coinbase zerlegen")
    # Von Hand gebaute Coinbase: Fassung, SegWit-Marke, ein Eingang, Nullen,
    # 0xFFFFFFFF, Skriptlaenge, Skript (BIP-34-Hoehe 962738 + Marke).
    skript = bytes([3]) + (962738).to_bytes(3, "little") + b"/F2Pool/xyz"
    roh = (bytes.fromhex("01000000") + b"\x00\x01" + b"\x01"
           + b"\x00" * 32 + b"\xff\xff\xff\xff"
           + bytes([len(skript)]) + skript + b"\xff\xff\xff\xff")
    pruefe("Skript gefunden", _coinbase_skript(roh.hex()), skript)
    pruefe("BIP-34-Hoehe gelesen", bip34_hoehe(skript), 962738)
    pruefe("Pool erkannt", pool_name(skript), "F2Pool")
    pruefe("Rohtext gelesen", lesbarer_lauf(skript), "/F2Pool/xyz")
    pruefe("unbekannter Pool wird NICHT geraten",
           pool_name(b"\x03\x00\x00\x00/irgendwas/"), None)
    pruefe("keine Coinbase -> nichts",
           _coinbase_skript((bytes.fromhex("01000000") + b"\x01"
                             + b"\x11" * 32 + b"\x00\x00\x00\x00"
                             + b"\x02ab" + b"\xff\xff\xff\xff").hex()), None)
    pruefe("Muell -> nichts", _coinbase_skript("nicht hex"), None)
    pruefe("leer -> nichts", _coinbase_skript(""), None)
    # Genau die Verwechslung, gegen die BIP 34 schuetzt.
    pruefe("Hoehe passt nicht zum Block", bip34_hoehe(skript) == 962739, False)

    print("\nBTC-Text")
    pruefe("englisch", btc(312500000, 3, "en"), "3.125")
    pruefe("deutsch", btc(312500000, 3, "de"), "3,125")
    pruefe("Tausender englisch", btc(750000000000, 3, "en"), "7,500.000")
    pruefe("Tausender deutsch", btc(750000000000, 3, "de"), "7.500,000")
    pruefe("wird gerundet, nicht abgeschnitten", btc(99999999, 3, "en"), "1.000")
    pruefe("None wird zum Strich", btc(None), "–")

    print("\nFenster: Fuellung, Geld, Takt")
    stats = [stat(100 + i, 1000 + i * 600) for i in range(5)]
    stats[2]["txs"] = 1                     # leerer Block
    stats[2]["total_weight"] = 4000
    stats[3]["time"] = stats[2]["time"] - 120   # Zeitstempel laeuft rueckwaerts
    stats[4]["time"] = stats[3]["time"] + 2400
    d = auswerten(stats, vorlaeufer_zeit=400, spitze=104, jetzt=10000)
    pruefe("alle Bloecke da", d["fenster"], 5)
    pruefe("leerer Block gefunden", d["leere"], [102])
    pruefe("auffaellig ist er auch", d["auffaellige"], [102])
    pruefe("Fuellung des vollen Blocks", round(d["bloecke"][0]["fuellung"], 1), 97.5)
    # Vier Bloecke zu 97,5 % und einer zu 0,1 % - der leere zieht den Schnitt
    # um zwanzig Punkte. Genau deshalb steht er auf der Seite auch einzeln da.
    pruefe("mittlere Fuellung", round(d["fuellung_mittel"], 1), 78.0)
    pruefe("Ruecklauf gezaehlt", d["rueckwaerts"], 1)
    pruefe("Ruecklauf steht am richtigen Block",
           [b["hoehe"] for b in d["bloecke"] if b["rueckwaerts"]], [103])
    pruefe("negativer Abstand bleibt negativ", d["abstand_min"], -120)
    pruefe("laengster Abstand", d["abstand_max"], 2400)
    pruefe("Median der Abstaende", d["abstand_median"], 600)
    pruefe("Nulllinie liegt nicht am Boden", round(d["takt_oben"], 1), 95.2)
    pruefe("Gebuehrenanteil am Lohn", round(d["gebuehren_anteil"], 2), 1.26)
    pruefe("nach der Halbierung waere er doppelt so gross",
           round(d["anteil_nach_halbierung"], 2), 2.50)
    pruefe("Belohnungsepoche", d["epoche"], 0)
    # Gemessen vom aeltesten bis zum juengsten Zeitstempel (1000 bis 4480),
    # nicht aus der Blockzahl gerechnet - der Ruecklauf steckt mit drin.
    pruefe("Fensterbreite in Stunden", round(d["fenster_stunden"], 2), 0.97)
    # Der Massstab der Fuellung ist die Konsensgrenze, NICHT der vollste Block.
    pruefe("volle Saeule bleibt unter 100", round(d["bloecke"][0]["fuellung_saeule"], 1), 97.5)
    pruefe("leerer Block bleibt sichtbar", d["bloecke"][2]["fuellung_saeule"], 2.0)

    print("\nMiner-Verteilung")
    marken = {100: {"text": "/F2Pool/x", "name": "F2Pool"},
              101: {"text": "/F2Pool/y", "name": "F2Pool"},
              102: {"text": "/irgendwas/", "name": None},
              103: {"text": None, "name": None}}
    d = auswerten(stats, marken, vorlaeufer_zeit=400, spitze=104, jetzt=10000)
    pruefe("zwei Gruppen", len(d["miner"]), 2)
    pruefe("haeufigster zuerst", d["miner"][0]["name"], "F2Pool")
    pruefe("erkannte Bloecke gezaehlt", d["miner_erkannt"], 2)
    pruefe("namenlose Gruppe am Ende", d["miner"][-1]["erkannt"], False)
    pruefe("namenlose Gruppe zaehlt beide", d["miner"][-1]["anzahl"], 2)
    pruefe("leerer Block dem Richtigen zugeschrieben", d["miner"][-1]["leer"], 1)
    pruefe("Block ohne Marke bleibt ohne Miner", d["bloecke"][4]["miner"], None)

    print("\nTaktbild: eine Skala fuer beide Richtungen")
    pruefe("laengster Abstand fuellt die obere Haelfte",
           d["bloecke"][4]["abstand_saeule"], 100.0)
    pruefe("tiefster Ruecklauf fuellt die untere Haelfte",
           d["bloecke"][3]["abstand_saeule"], 100.0)
    # 600 s von 2400 s Hoehe der oberen Haelfte - ein Viertel ueber der Null.
    pruefe("Zielmarke sitzt bei einem Viertel", round(d["ziel_marke"], 1), 25.0)

    print("\nAusfaelle: was fehlt, fehlt einzeln")
    kaputt = [stat(200, 5000), None, stat(202, 6200)]
    d = auswerten(kaputt, spitze=202, jetzt=10000, hoehen=[200, 201, 202])
    pruefe("nur zwei Bloecke haben Zahlen", d["fenster"], 2)
    pruefe("die Luecke behaelt ihren Platz", len(d["bloecke"]), 3)
    pruefe("die Luecke hat keine Saeule", d["bloecke"][1]["fuellung_saeule"], None)
    pruefe("die Luecke bleibt anklickbar", d["bloecke"][1]["hoehe"], 201)
    # Ueber eine Luecke hinweg ist der Abstand UNBEKANNT - nicht die Summe
    # zweier Abstaende. Sonst saehe der Block nach der Luecke jedes Mal wie ein
    # ungewoehnlich langsamer aus.
    pruefe("kein Abstand ueber die Luecke hinweg", d["bloecke"][2]["abstand"], None)
    pruefe("und auch keine Saeule", d["bloecke"][2]["abstand_saeule"], None)
    pruefe("erster Block ohne Vorlaeufer hat keinen Abstand",
           d["bloecke"][0]["abstand"], None)

    ohne = auswerten([], spitze=None, jetzt=10000)
    pruefe("gar keine Daten sind kein Absturz", ohne["erreichbar"], False)
    pruefe("keine Miner ohne Daten", ohne["miner"], [])
    pruefe("kein Median ohne Abstaende", ohne["abstand_median"], None)
    pruefe("kein Ziel ohne Abstaende", ohne["ziel_marke"], None)

    print("\nGegen den Probeknoten (24 Bloecke, kein echter Knoten)")
    from .probeknoten import ProbeTor
    tor = ProbeTor()
    d = asyncio.run(uebersicht(tor, "de", jetzt=10000))
    pruefe("24 Bloecke geholt", d["fenster"], 24)
    pruefe("kein einziger teurer Aufruf",
           sorted({m for m, _ in tor.aufrufe if m not in
                   ("getblockchaininfo", "getblockstats")}), [])
    pruefe("nur Felder, die wir brauchen",
           [a[1][1] for a in tor.aufrufe if a[0] == "getblockstats"][0],
           STATSFELDER)
    pruefe("Belohnung des Fensters", d["btc"](d["subsidy_sat"]), "75,000")
    tot = ProbeTor(ausfall={"getblockchaininfo"})
    pruefe("ohne Kettenspitze bleibt die Seite stehen",
           asyncio.run(uebersicht(tot))["erreichbar"], False)

    print("\n%d Fehler" % len(fehler))
    for f in fehler:
        print("  " + f)
    return 1 if fehler else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selbsttest())
