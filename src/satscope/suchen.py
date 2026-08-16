"""Eine Eingabezeile fuer alles - erkennen, was der Nutzer da eingegeben hat.

Die groesste Luecke des Projekts sass hier: /suche erkannte AUSSCHLIESSLICH
Adressen. Wer eine Txid, eine Blockhoehe oder einen Blockhash eintippte, bekam
"nicht erkannt" - obwohl es fuer alle drei laengst eine eigene Seite gibt.

ZWEI STUFEN, und die Trennung ist der ganze Entwurf:

    zerlegen(text)        entscheidet allein aus der FORM. Kein Netz, kein
                          Knoten, keine Wartezeit - und damit ohne laufenden
                          bitcoind pruefbar.
    einordnen(tor, text)  fragt den Knoten nur dort, wo die Form nicht reicht.

Die Form reicht an genau zwei Stellen nicht:

  * 64 Hexziffern sind ein Blockhash ODER eine Txid. Beide sehen exakt gleich
    aus, es gibt kein Zeichen, das sie unterscheidet. Also: erst
    getblockheader (<5 ms) fragen, und nur wenn das nichts weiss,
    getrawtransaction (19-28 ms). Die Reihenfolge ist die billige.
  * Eine Ziffernfolge ist eine Blockhoehe - aber nur bis zur Kettenspitze.
    Die kostet getblockchaininfo, 9 ms.

Teuerster Fall ist damit eine Eingabe, die weder Block noch Transaktion ist:
<5 + 28 ms. Kein einziger Aufruf ausserhalb der Kostenklasse BILLIG.

WAS ZURUECKKOMMT: (art, ziel_pfad) - oder (None, None), wenn die Eingabe
nichts von alledem ist. `art` sagt, WORAUF man gestossen ist; `ziel_pfad` ist
der Ort, an den umgeleitet werden soll. Wer nur umleiten will, braucht nur den
zweiten Wert und darf jede Art gleich behandeln.

    "hoehe"          eine Hoehe, die es gibt              -> /block/<n>
    "hoehe_zukunft"  eine Hoehe oberhalb der Spitze       -> /block/<n>
    "block"          64 Hex, der Knoten kennt den Block   -> /block/<hash>
    "tx"             64 Hex, der Knoten kennt die Tx      -> /tx/<txid>
    "hex"            64 Hex, der Knoten kennt beides nicht-> /tx/<hex>
    "adresse"        gueltige Adresse (Base58/Bech32/32m) -> /address/<adr>
    "xpub"           erweiterter OEFFENTLICHER Schluessel -> /xpub/<xpub>
    "xprv"           erweiterter PRIVATER Schluessel      -> /xpub/<xprv>

WARUM AUCH DIE DREI UNSICHEREN FAELLE EIN ZIEL BEKOMMEN: die Zielseiten
erklaeren den Fehlschlag besser als eine Suchzeile es koennte. /block/9999999
nennt die eigene Kettenspitze; /tx/<hex> erklaert, dass dieser Knoten keinen
Transaktionsindex fuehrt und wie man ihn einschaltet. Ein pauschales "nicht
erkannt" waere an dieser Stelle die schlechtere, weil aermere Antwort.

⚠️ ZWEI DINGE ZUM SCHLUESSEL-FALL, die eine Entscheidung brauchen:
  * Die Route /xpub/<...> muss es geben. xpub.py liegt im Baum und bringt die
    Auswertung mit; verdrahtet wird sie in web.py, nicht hier.
  * "xprv" bekommt ABSICHTLICH ein Ziel und kein stummes Nein: wer seinen
    privaten Schluessel in die Suchzeile klebt, muss die Warnung sehen, sonst
    versucht er es als naechstes bei einem Explorer im Netz. Der Schluessel
    steht dann allerdings in einer zweiten Adresszeile (die erste ist
    /suche?q=... und damit ohnehin schon im Verlauf). Wer das nicht will,
    behandelt die Art "xprv" im Aufrufer und leitet NICHT um - genau dafuer
    steht sie getrennt da.
"""
import asyncio
import hashlib

from . import adresse
from .rpc import RpcFehler

# Kleinschreibung ist die kanonische Form eines Hashes; Grossbuchstaben werden
# vorher umgeschrieben.
HEXZIFFERN = frozenset("0123456789abcdef")

# Ein Hash hat 64 Hexziffern (32 Byte). Weder mehr noch weniger.
HEXLAENGE = 64

# Bis zu neun Ziffern - das sind 999.999.999 moegliche Bloecke, rund 19.000
# Jahre Kette. Die Grenze steht nicht gegen den Nutzer, sondern dagegen, eine
# tausendstellige Zahl an bitcoind weiterzureichen.
MAX_HOEHENSTELLEN = 9

# Ein erweiterter Schluessel ist Base58Check ueber genau 78 Byte: 4 Version,
# 1 Tiefe, 4 Fingerabdruck der Eltern, 4 Kindnummer, 32 Kettencode, 33 Punkt.
# Erkannt wird an DIESER Laenge und nicht an einem Vorsatz aus xpub/ypub/zpub -
# dieselbe Regel, nach der xpub.zerlegen() arbeitet. Damit landen auch tpub,
# upub, vpub und die Mehrsignatur-Formen (Ypub/Zpub) auf der Seite, die sie
# auseinanderhalten und den Unterschied erklaeren kann. Ein Vorsatzvergleich
# haette sie hier stumm als "nicht erkannt" abgewiesen.
XPUB_NUTZLAST = 78

# An dieser Stelle steht bei einem PRIVATEN erweiterten Schluessel (xprv) ein
# Fuellbyte 0x00, bei einem oeffentlichen der Punkt (0x02/0x03). Das gilt
# versionsunabhaengig - deshalb wird hier am Byte geprueft und nicht an einer
# Versionsliste.
XPRV_MARKE = 45

# ⚠️ Laengendeckel, und zwar aus einem gemessenen Grund: die Base58-Pruefung in
# adresse.py rechnet die Eingabe in EINE grosse Zahl um, was quadratisch mit
# der Laenge waechst. Hier gemessen (16.08.2026): 8.000 Zeichen 7 ms, 32.000
# Zeichen 99 ms, 65.000 Zeichen 404 ms - und diese Zeit blockiert, weil sie
# reine Rechnung ist, die GANZE Anwendung (ein Prozess, eine Schleife). Der
# laengste sinnvolle Wert ist ein xpub mit 112 Zeichen; 200 sind reichlich.
MAX_LAENGE = 200


async def _sicher(tor, methode, *argumente):
    """Ruft auf und liefert None statt zu werfen - wie knoten._sicher.

    Noch einmal hier und nicht importiert, wie in blockseite und kette auch:
    die Regel "ein ausgefallener Aufruf kostet genau seinen Wert, nie die
    Seite" soll in jedem Modul lesbar dastehen, das sie anwendet. Hier heisst
    sie: ein stummer Knoten macht aus der Suche keine Fehlermeldung, sondern
    schickt weiter an die Seite, die den Ausfall erklaeren kann.
    """
    try:
        return await tor.ruf(methode, *argumente)
    except (RpcFehler, OSError, asyncio.TimeoutError):
        return None


# Unsichtbares, das beim Kopieren aus PDF, Chat oder Tabelle mitkommt und das
# Pythons strip() NICHT als Leerraum kennt: Breitenloses Leerzeichen, die
# beiden Verbinder und die Byte-Reihenfolge-Marke. Das geschuetzte Leerzeichen
# U+00A0 faellt bereits unter str.strip() und steht deshalb nicht hier.
UNSICHTBAR = "\u200b\u200c\u200d\ufeff"


def saeubern(eingabe):
    """Die Eingabe so, wie sie gemeint war.

    Abgeschnitten wird nur AUSSEN herum. Innen bleibt alles stehen: ein
    Leerzeichen mitten in einer Adresse ist ein Fehler und soll einer bleiben -
    wer ihn stillschweigend wegputzt, sucht am Ende etwas anderes als das
    Eingegebene.
    """
    return (eingabe or "").strip().strip(UNSICHTBAR).strip()


def _ohne_0x(text):
    """"0x" vorne weg. Wer einen Hash aus einem Werkzeug kopiert, hat ihn oft
    mit dabei; Bitcoin selbst schreibt ihn nie."""
    if text[:2].lower() == "0x":
        return text[2:]
    return text


def _ist_hex(text):
    return len(text) == HEXLAENGE and set(text) <= HEXZIFFERN


def _erweiterter_schluessel(text):
    """Die 78 Byte eines erweiterten Schluessels - oder None.

    Die Pruefsumme wird wirklich gerechnet und nicht der Vorsatz geglaubt:
    sonst ginge jeder Tippfehler in einem xpub an eine Seite, die ihn nur
    ratlos anschauen kann. Das Alphabet kommt aus adresse.B58 - eine zweite
    Abschrift der 58 Zeichen ist genau die Stelle, an der in diesem Projekt
    schon einmal ein Zeichen zu viel stand (s. adresse.py).

    Die Dekodierung steht hier noch einmal und wird nicht aus adresse.py
    geholt: dort ist sie privat (_base58check), und ein privater Name aus einem
    fremden Modul ist eine Fessel, die beim naechsten Umbau reisst.
    """
    zahl = 0
    for z in text:
        if z not in adresse.B58:
            return None
        zahl = zahl * 58 + adresse.B58.index(z)
    roh = zahl.to_bytes((zahl.bit_length() + 7) // 8, "big")
    roh = b"\x00" * (len(text) - len(text.lstrip("1"))) + roh
    if len(roh) != XPUB_NUTZLAST + 4:
        return None
    nutz, pruef = roh[:-4], roh[-4:]
    if hashlib.sha256(hashlib.sha256(nutz).digest()).digest()[:4] != pruef:
        return None
    return nutz


def zerlegen(eingabe):
    """(form, wert) allein aus der Gestalt der Eingabe. Ohne Knoten, ohne Netz.

        ("hex", "0000…")   64 Hexziffern - Blockhash ODER Txid, hier noch offen
        ("hoehe", 962618)  reine Ziffern
        ("adresse", "bc1…") von adresse.py als gueltig abgenommen
        ("xpub", "zpub…")  erweiterter oeffentlicher Schluessel
        (None, None)       nichts davon

    REIHENFOLGE: der Hex-Fall steht VOR dem Ziffern-Fall. 64 Ziffern sind
    zugleich gueltiges Hex und eine gueltige Zahl - aber eine Blockhoehe mit 64
    Stellen gibt es nicht, ein Hash aus lauter Ziffern schon.
    """
    text = saeubern(eingabe)
    if not text or len(text) > MAX_LAENGE:
        return None, None

    kern = _ohne_0x(text)
    if _ist_hex(kern.lower()):
        return "hex", kern.lower()

    # isascii() gehoert dazu: isdigit() ist auch fuer arabisch-indische Ziffern
    # wahr, und was wir nicht selbst lesen koennen, reichen wir nicht weiter.
    if text.isascii() and text.isdigit() and len(text) <= MAX_HOEHENSTELLEN:
        return "hoehe", int(text)

    try:
        adresse.scripthash(text)
        return "adresse", text
    except adresse.UnbekannteAdresse:
        pass
    except IndexError:
        # ⚠️ KEIN Schoenheitsfehler: "bc1gmk9yu" hat eine gueltige
        # Bech32-Pruefsumme bei LEERER Nutzlast; adresse.py greift dann auf
        # werte[0] zu und wirft IndexError statt UnbekannteAdresse (hier am
        # 16.08.2026 gefunden und nachgestellt). Aus einer Suchzeile heraus
        # waere das ein HTTP 500 auf eine getippte Eingabe. Gefangen wird es
        # hier, repariert gehoert es in adresse.py - dort steht es nicht in
        # meiner Zustaendigkeit.
        pass

    nutz = _erweiterter_schluessel(text)
    if nutz is not None:
        # ⚠️ Ein PRIVATER erweiterter Schluessel bekommt eine eigene Art, damit
        # der Aufrufer ihn nicht wie einen oeffentlichen behandeln MUSS. Wer
        # seinen xprv versehentlich in die Suchzeile klebt, soll die Warnung
        # dazu sehen - sonst probiert er es als naechstes bei einem Explorer im
        # Netz, und dann ist das Geld weg.
        return ("xprv" if nutz[XPRV_MARKE] == 0x00 else "xpub"), text

    return None, None


async def einordnen(tor, eingabe):
    """(art, ziel_pfad) fuer eine Sucheingabe - oder (None, None).

    Die Pfade werden ausschliesslich aus geprueften Zeichenvorraeten gebaut
    (Hexziffern, Dezimalziffern, Base58/Bech32). Was zerlegen() nicht abgenommen
    hat, kommt hier gar nicht erst an - deshalb kann in einem Pfad kein
    Schraegstrich und kein Fragezeichen landen.
    """
    form, wert = zerlegen(eingabe)

    if form == "hoehe":
        # Die Spitze kostet 9 ms und trennt "gibt es noch nicht" von "gibt es".
        # Beide Male wird trotzdem auf die Blockseite geschickt: sie nennt die
        # Spitze im Klartext, was hier niemand liest.
        kette = await _sicher(tor, "getblockchaininfo")
        spitze = (kette or {}).get("blocks")
        if isinstance(spitze, int) and wert > spitze:
            return "hoehe_zukunft", "/block/%d" % wert
        return "hoehe", "/block/%d" % wert

    if form == "hex":
        # Erst der Block: getblockheader kostet <5 ms und antwortet auf eine
        # Txid schlicht mit einem Fehler - eine Verwechslung ist ausgeschlossen.
        if isinstance(await _sicher(tor, "getblockheader", wert), dict):
            return "block", "/block/" + wert
        # Dann die Transaktion. Ohne Verbose-Stufe: wir wollen nur wissen, ob es
        # sie gibt, und die rohe Form ist die billigste Antwort darauf.
        if await _sicher(tor, "getrawtransaction", wert):
            return "tx", "/tx/" + wert
        # Beides nichts. Das heisst NICHT "gibt es nicht": ohne txindex kann
        # dieser Knoten bestaetigte Transaktionen gar nicht nachschlagen. Genau
        # das erklaert die Transaktionsseite - also dorthin.
        return "hex", "/tx/" + wert

    if form == "adresse":
        return "adresse", "/address/" + wert

    if form in ("xpub", "xprv"):
        return form, "/xpub/" + wert

    return None, None
