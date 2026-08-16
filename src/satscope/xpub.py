"""Eine ganze Wallet aus ihrem oeffentlichen Schluessel - auf dem eigenen Knoten.

WARUM DIESE SEITE DAS STAERKSTE ARGUMENT FUERS SELBERHOSTEN IST
Ein erweiterter oeffentlicher Schluessel (xpub) beschreibt nicht eine Adresse,
sondern alle Adressen einer Wallet - vergangene wie kuenftige. Wer ihn bei einem
oeffentlichen Explorer eingibt, uebergibt dem Betreiber dauerhaft den Einblick in
jede Zahlung, die er je empfangen oder gesendet hat; ein einziges Eingabefeld
hebt damit die gesamte Muenz-Trennung auf, um die sich eine Wallet sonst bemueht.
Auf dem eigenen Knoten passiert genau dasselbe - nur sieht es niemand. Deshalb
rechnet dieses Modul ALLES selbst: die Ableitung laeuft in diesem Prozess, die
Salden kommen vom Electrum-Index im eigenen Haus, und nach draussen geht kein
einziges Byte.

WAS HIER GERECHNET WIRD
Kindschluessel entstehen nach BIP32 aus dem Elternpunkt:
    I  = HMAC-SHA512(Kettencode, serP(K_eltern) || ser32(i))
    K_i = I[:32] * G + K_eltern
Der zweite Summand ist eine Punktaddition auf secp256k1 - deshalb steht hier
Kurvenarithmetik in reinem Python. ⚠️ Das geht NUR fuer nicht gehaertete Indizes
(i < 2^31); gehaertete brauchen den privaten Schluessel, und den will man auf
einem Server nicht einmal in der Naehe haben.

BELEGT, NICHT BEHAUPTET (selbsttest() am Ende dieser Datei, `python3 -m satscope.xpub`)
Eine falsche Ableitung liefert wunderschoene, voellig fremde Adressen - der
Fehler faellt niemandem auf, bis jemand Geld an eine davon schickt. Die Rechnung
wird deshalb gegen veroeffentlichte Vektoren geprueft:
  * BIP32, Testvektoren 1 und 2: sechs nicht gehaertete Ableitungsschritte,
    jeweils bis zur fertigen xpub-Zeichenkette verglichen.
  * BIP84: zpub6rFR7y4Q2Aij... -> die drei dort abgedruckten Adressen
    (0/0, 0/1, 1/0) - der komplette Weg, den auch diese Seite geht.
  * BIP49: upub5EFU65HtV5Tei... -> 2Mww8dCYPUpKHofjgcXcBCEGmniw9CoaiD2,
    dazu die dort abgedruckten Zwischenwerte HASH160 und Einloeseskript.
  * RIPEMD-160 gegen die Vektoren des Urautors ("abc", "" und weitere).

KOSTEN
Kein einziger RPC-Aufruf an bitcoind. Der Knoten wird von dieser Seite gar nicht
behelligt; gefragt wird nur der Electrum-Index, und zwar
blockchain.scripthash.get_history je Adresse und get_balance nur fuer die
Adressen, die ueberhaupt eine Historie haben. Eine Adresse ohne Historie hat
zwingend Saldo null - das ist keine Schaetzung, sondern Definition, und spart
bei einer frischen Wallet die Haelfte aller Abfragen.
"""
import asyncio
import hashlib
import hmac

from . import adresse, elektrum
from .sprache import STANDARD

# --------------------------------------------------------------- Stellschrauben
# Die uebliche Regel ("gap limit"): erst wenn 20 Adressen hintereinander noch nie
# benutzt wurden, gilt die Wallet als zu Ende gelesen. 20 ist der Wert, auf den
# sich die Wallets geeinigt haben - wer weniger nimmt, uebersieht Geld.
LUECKE = 20
# So viele Adressen holen wir je Runde. Entspricht der Luecke, damit im Normalfall
# (frische Wallet) genau eine Runde je Kette reicht.
RUNDE = 20
# Sicherheitsdeckel je Kette. Ohne ihn koennte eine Wallet mit sehr vielen
# benutzten Adressen den Index minutenlang beschaeftigen. Wird er erreicht, sagt
# die Seite das ausdruecklich - abgeschnitten, nicht "fertig".
HOECHSTENS = 120
# So viele Verbindungen zum Electrum-Server gleichzeitig. Fulcrum erlaubt in der
# Voreinstellung mehr, aber wir sind Gast: vier Straenge holen 40 Adressen in
# einer Zehntelsekunde, und mehr braucht kein Mensch.
STRAENGE = 4

# Die beiden Ketten einer Wallet (BIP44): 0 = Empfang, 1 = Wechselgeld.
EMPFANG, WECHSEL = 0, 1

GEHAERTET = 0x80000000


class UngueltigerSchluessel(ValueError):
    """Keine verwendbare erweiterte oeffentliche Schluessel-Zeichenkette.

    Traegt in `grund` einen Schluessel fuer den Textkatalog, damit die Seite den
    Unterschied zwischen "Tippfehler" und "das war Ihr PRIVATER Schluessel"
    sagen kann - das ist kein Detail, sondern der wichtigste Satz der Seite.
    """

    def __init__(self, grund, zusatz=None):
        super().__init__(grund)
        self.grund = grund
        self.zusatz = zusatz


# --------------------------------------------------------------- RIPEMD-160
# ⚠️ Warum das hier in voller Laenge steht: hashlib.new("ripemd160") gibt es nur,
# wenn OpenSSL den Legacy-Provider geladen hat. Auf Fedora ist er da, im Image
# python:3.13-slim (Debian) NICHT - und ohne RIPEMD-160 gibt es keine einzige
# Bitcoin-Adresse. Ein Ausfall genau dort waere der klassische
# "laeuft-bei-mir"-Fehler: Selbsttest gruen, Container tot. Also rechnen wir es
# selbst, wenn OpenSSL nicht mitspielt. Kosten: rund 60 Mikrosekunden je Adresse
# - bei 240 Adressen 15 ms, und damit voellig gleichgueltig.
_R_LINKS = (
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
    7, 4, 13, 1, 10, 6, 15, 3, 12, 0, 9, 5, 2, 14, 11, 8,
    3, 10, 14, 4, 9, 15, 8, 1, 2, 7, 0, 6, 13, 11, 5, 12,
    1, 9, 11, 10, 0, 8, 12, 4, 13, 3, 7, 15, 14, 5, 6, 2,
    4, 0, 5, 9, 7, 12, 2, 10, 14, 1, 3, 8, 11, 6, 15, 13)
_R_RECHTS = (
    5, 14, 7, 0, 9, 2, 11, 4, 13, 6, 15, 8, 1, 10, 3, 12,
    6, 11, 3, 7, 0, 13, 5, 10, 14, 15, 8, 12, 4, 9, 1, 2,
    15, 5, 1, 3, 7, 14, 6, 9, 11, 8, 12, 2, 10, 0, 4, 13,
    8, 6, 4, 1, 3, 11, 15, 0, 5, 12, 2, 13, 9, 7, 10, 14,
    12, 15, 10, 4, 1, 5, 8, 7, 6, 2, 13, 14, 0, 3, 9, 11)
_S_LINKS = (
    11, 14, 15, 12, 5, 8, 7, 9, 11, 13, 14, 15, 6, 7, 9, 8,
    7, 6, 8, 13, 11, 9, 7, 15, 7, 12, 15, 9, 11, 7, 13, 12,
    11, 13, 6, 7, 14, 9, 13, 15, 14, 8, 13, 6, 5, 12, 7, 5,
    11, 12, 14, 15, 14, 15, 9, 8, 9, 14, 5, 6, 8, 6, 5, 12,
    9, 15, 5, 11, 6, 8, 13, 12, 5, 12, 13, 14, 11, 8, 5, 6)
_S_RECHTS = (
    8, 9, 9, 11, 13, 15, 15, 5, 7, 7, 8, 11, 14, 14, 12, 6,
    9, 13, 15, 7, 12, 8, 9, 11, 7, 7, 12, 7, 6, 15, 13, 11,
    9, 7, 15, 11, 8, 6, 6, 14, 12, 13, 5, 14, 13, 13, 7, 5,
    15, 5, 8, 11, 14, 14, 6, 14, 6, 9, 12, 9, 12, 5, 15, 8,
    8, 5, 12, 9, 12, 5, 14, 6, 8, 13, 6, 5, 15, 13, 11, 11)
_K_LINKS = (0x00000000, 0x5A827999, 0x6ED9EBA1, 0x8F1BBCDC, 0xA953FD4E)
_K_RECHTS = (0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0x7A6D76E9, 0x00000000)
_MASKE = 0xFFFFFFFF


def _dreh(wert, um):
    """Linksrotation in 32 Bit."""
    wert &= _MASKE
    return ((wert << um) | (wert >> (32 - um))) & _MASKE


def _f(runde, x, y, z):
    if runde < 16:
        return x ^ y ^ z
    if runde < 32:
        return (x & y) | (~x & _MASKE & z)
    if runde < 48:
        return (x | (~y & _MASKE)) ^ z
    if runde < 64:
        return (x & z) | (y & (~z & _MASKE))
    return x ^ (y | (~z & _MASKE))


def ripemd160_rein(daten):
    """RIPEMD-160 in reinem Python. Gleiches Ergebnis wie OpenSSL, nur langsamer."""
    h = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0]
    # Polsterung wie bei MD4/MD5: 0x80, Nullen, dann die Laenge in Bit
    # KLEIN-zuerst. Big-endian waere hier der Klassiker unter den Fehlern.
    laenge = len(daten) * 8
    daten = daten + b"\x80"
    daten += b"\x00" * ((56 - len(daten) % 64) % 64)
    daten += laenge.to_bytes(8, "little")

    for anfang in range(0, len(daten), 64):
        x = [int.from_bytes(daten[anfang + 4 * i:anfang + 4 * i + 4], "little")
             for i in range(16)]
        a, b, c, d, e = h
        a2, b2, c2, d2, e2 = h
        for j in range(80):
            t = _dreh((a + _f(j, b, c, d) + x[_R_LINKS[j]]
                       + _K_LINKS[j // 16]) & _MASKE, _S_LINKS[j])
            t = (t + e) & _MASKE
            a, e, d, c, b = e, d, _dreh(c, 10), b, t
            t = _dreh((a2 + _f(79 - j, b2, c2, d2) + x[_R_RECHTS[j]]
                       + _K_RECHTS[j // 16]) & _MASKE, _S_RECHTS[j])
            t = (t + e2) & _MASKE
            a2, e2, d2, c2, b2 = e2, d2, _dreh(c2, 10), b2, t
        t = (h[1] + c + d2) & _MASKE
        h[1] = (h[2] + d + e2) & _MASKE
        h[2] = (h[3] + e + a2) & _MASKE
        h[3] = (h[4] + a + b2) & _MASKE
        h[4] = (h[0] + b + c2) & _MASKE
        h[0] = t
    return b"".join(w.to_bytes(4, "little") for w in h)


def _ripemd160_openssl():
    """Liefert die schnelle Variante - oder None, wenn OpenSSL sie nicht fuehrt."""
    try:
        hashlib.new("ripemd160", b"").digest()
    except (ValueError, TypeError):
        return None
    return lambda daten: hashlib.new("ripemd160", daten).digest()


_RIPEMD = _ripemd160_openssl() or ripemd160_rein


def hash160(daten):
    """RIPEMD-160(SHA-256(x)) - die Bitcoin-Kurzform eines Schluessels."""
    return _RIPEMD(hashlib.sha256(daten).digest())


# --------------------------------------------------------------- secp256k1
# Alle vier Zahlen sind Definition (SEC 2, secp256k1), keine Wahl. Die Kurve ist
# y² = x³ + 7 ueber dem Primkoerper P; N ist die Ordnung des Erzeugers G.
P = 2**256 - 2**32 - 977
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8

# ⚠️ Gerechnet wird in Jacobi-Koordinaten (X, Y, Z) mit x = X/Z², y = Y/Z³.
# Der Grund ist reine Geschwindigkeit: in affinen Koordinaten braucht JEDE
# Punktaddition eine modulare Inversion (pow(x, P-2, P), rund 25 Mikrosekunden),
# das sind bei einer Ableitung ueber 380 Stueck - gemessen 12 ms je Adresse und
# damit 2,9 s fuer 240 Adressen. In Jacobi-Koordinaten faellt genau EINE
# Inversion je Kindschluessel an; gemessen 1,3 ms je Adresse.
# Z = 0 ist der unendlich ferne Punkt.
_UNENDLICH = (0, 0, 0)


def _verdopple(punkt):
    """2*P in Jacobi-Koordinaten (dbl-2009-l, gueltig weil a = 0)."""
    x, y, z = punkt
    if not y or not z:
        return _UNENDLICH
    a = x * x % P
    b = y * y % P
    c = b * b % P
    d = 2 * ((x + b) * (x + b) - a - c) % P
    e = 3 * a % P
    f = e * e % P
    x3 = (f - 2 * d) % P
    return (x3, (e * (d - x3) - 8 * c) % P, 2 * y * z % P)


def _addiere_affin(punkt, x2, y2):
    """Jacobi-Punkt + affiner Punkt. Der Summand hat Z = 1, das spart Arbeit."""
    x1, y1, z1 = punkt
    if not z1:
        return (x2, y2, 1)
    zz = z1 * z1 % P
    u2 = x2 * zz % P
    s2 = y2 * zz % P * z1 % P
    h = (u2 - x1) % P
    r = (s2 - y1) % P
    if not h:
        # Gleicher x-Wert: entweder derselbe Punkt (verdoppeln) oder sein
        # Negatives (Summe ist der unendlich ferne Punkt).
        return _verdopple(punkt) if not r else _UNENDLICH
    h2 = h * h % P
    h3 = h2 * h % P
    v = x1 * h2 % P
    x3 = (r * r - h3 - 2 * v) % P
    return (x3, (r * (v - x3) - y1 * h3) % P, z1 * h % P)


def _mal_g(k):
    """k*G, von oben nach unten verdoppelt und addiert."""
    erg = _UNENDLICH
    for bit in range(k.bit_length() - 1, -1, -1):
        erg = _verdopple(erg)
        if (k >> bit) & 1:
            erg = _addiere_affin(erg, GX, GY)
    return erg


def _nach_affin(punkt):
    """Zurueck auf (x, y) - hier faellt die einzige Inversion an."""
    x, y, z = punkt
    if not z:
        return None
    zi = pow(z, P - 2, P)
    zi2 = zi * zi % P
    return (x * zi2 % P, y * zi2 % P * zi % P)


def punkt_aus_bytes(roh):
    """33 Byte gestauchter Punkt -> (x, y).

    y wird aus x zurueckgerechnet: y² = x³ + 7. Weil P ≡ 3 (mod 4) ist die
    Wurzel schlicht y = (x³+7)^((P+1)/4). Das Ergebnis MUSS anschliessend
    geprueft werden - fuer ein x, das auf keinem Kurvenpunkt liegt, liefert die
    Formel trotzdem eine Zahl.
    """
    if len(roh) != 33 or roh[0] not in (2, 3):
        raise UngueltigerSchluessel("punkt")
    x = int.from_bytes(roh[1:], "big")
    if x >= P:
        raise UngueltigerSchluessel("punkt")
    yy = (pow(x, 3, P) + 7) % P
    y = pow(yy, (P + 1) // 4, P)
    if y * y % P != yy:
        raise UngueltigerSchluessel("punkt")
    if y & 1 != roh[0] & 1:
        y = P - y
    return (x, y)


def bytes_aus_punkt(punkt):
    """(x, y) -> 33 Byte gestaucht. Das Vorzeichenbyte traegt die Paritaet von y."""
    x, y = punkt
    return bytes((2 + (y & 1),)) + x.to_bytes(32, "big")


# --------------------------------------------------------------- BIP32
def kind_ableiten(punkt, kettencode, index):
    """CKDpub: (Kindpunkt, Kindkettencode) - oder None fuer einen toten Index.

    ⚠️ Nur nicht gehaertet. Bei i >= 2^31 verlangt BIP32 den privaten
    Schluessel; aus einem xpub ist das nicht nur schwer, sondern unmoeglich.
    """
    if index >= GEHAERTET:
        raise UngueltigerSchluessel("gehaertet")
    i = hmac.new(kettencode, bytes_aus_punkt(punkt) + index.to_bytes(4, "big"),
                 hashlib.sha512).digest()
    links = int.from_bytes(i[:32], "big")
    # BIP32 sagt fuer diesen Fall ausdruecklich: Index ueberspringen, nicht etwa
    # modulo rechnen. Die Wahrscheinlichkeit liegt bei rund 1 zu 2^127 - der
    # Zweig ist da, weil "kommt nie vor" kein Argument ist, sondern eine Wette.
    if links >= N:
        return None
    neu = _nach_affin(_addiere_affin(_mal_g(links), *punkt))
    if neu is None:
        return None
    return neu, i[32:]


def fingerabdruck(punkt):
    """Die ersten vier Byte von HASH160 des Punktes - so verweist BIP32 auf Eltern."""
    return hash160(bytes_aus_punkt(punkt))[:4]


# --------------------------------------------------------------- Base58/Bech32
# Zerlegt wird mit adresse._base58check und adresse._polymod: derselbe Code, der
# schon jede Adresse dieser App prueft. Nur das ZUSAMMENSETZEN fehlt dort - es
# steht hier, weil bisher nichts in Satscope Adressen erzeugen musste.
def base58check(nutz):
    """Nutzlast + vier Pruefbyte, in Base58 geschrieben."""
    pruef = hashlib.sha256(hashlib.sha256(nutz).digest()).digest()[:4]
    roh = nutz + pruef
    zahl = int.from_bytes(roh, "big")
    aus = ""
    while zahl:
        zahl, rest = divmod(zahl, 58)
        aus = adresse.B58[rest] + aus
    # Fuehrende Nullbytes sind fuehrende Einsen - sie gehen in der Zahl verloren
    # und muessen einzeln nachgezaehlt werden.
    return "1" * (len(roh) - len(roh.lstrip(b"\x00"))) + aus


def _acht_auf_fuenf(daten):
    """Bytes in Fuenfergruppen - die Gegenrichtung zu adresse._fuenf_auf_acht."""
    speicher, bits, raus = 0, 0, []
    for b in daten:
        speicher = (speicher << 8) | b
        bits += 8
        while bits >= 5:
            bits -= 5
            raus.append((speicher >> bits) & 31)
    if bits:
        raus.append((speicher << (5 - bits)) & 31)
    return raus


def bech32(vorsatz, fassung, programm):
    """Zeugen-Programm als Bech32- (Fassung 0) oder Bech32m-Adresse."""
    werte = [fassung] + _acht_auf_fuenf(programm)
    konstante = 1 if fassung == 0 else adresse.BECH32M
    pruef = adresse._polymod(
        adresse._erweitere(vorsatz) + werte + [0] * 6) ^ konstante
    werte = werte + [(pruef >> 5 * (5 - i)) & 31 for i in range(6)]
    return vorsatz + "1" + "".join(adresse.BECH[w] for w in werte)


# --------------------------------------------------------------- Schluesselarten
# Vier Byte Version sagen, wie die Adressen dieses Schluessels aussehen. Die
# Zuordnung ist SLIP-132; erfunden hat sie nicht BIP32, sondern die Wallets, die
# ihren Nutzern ersparen wollten, den Ableitungspfad selbst einzutippen.
#              Name    Skriptart      Netz     P2PKH  P2SH  Bech32-Vorsatz
ARTEN = {
    0x0488B21E: ("xpub", "P2PKH", "main", 0x00, 0x05, "bc"),
    0x049D7CB2: ("ypub", "P2SH-P2WPKH", "main", 0x00, 0x05, "bc"),
    0x04B24746: ("zpub", "P2WPKH", "main", 0x00, 0x05, "bc"),
    0x043587CF: ("tpub", "P2PKH", "test", 0x6F, 0xC4, "tb"),
    0x044A5262: ("upub", "P2SH-P2WPKH", "test", 0x6F, 0xC4, "tb"),
    0x045F1CF6: ("vpub", "P2WPKH", "test", 0x6F, 0xC4, "tb"),
}

# Mehrsignatur-Schluessel (SLIP-132). Aus ihnen ALLEIN laesst sich keine Adresse
# bilden - dazu gehoeren die Schluessel der Mitunterzeichner und die Reihenfolge,
# in der sie im Skript stehen. Wir sagen das, statt etwas Falsches zu zeigen.
MEHRSIG = {0x0295B43F: "Ypub", 0x02AA7ED3: "Zpub",
           0x024289EF: "Vpub", 0x02575483: "Zpub"}


def adresse_und_skript(punkt, schluessel):
    """(Adresse, scriptPubKey) zu einem Kurvenpunkt.

    Beides zusammen, weil beides zusammengehoert: die Adresse geht auf die
    Seite, das Skript in die Electrum-Kennung. Wer sie getrennt rechnet,
    riskiert genau den Fehler, den niemand bemerkt - Adresse A ueber dem Saldo
    von Skript B.
    """
    kurz = hash160(bytes_aus_punkt(punkt))
    art = schluessel["skriptart"]
    if art == "P2PKH":
        return (base58check(bytes((schluessel["_p2pkh"],)) + kurz),
                b"\x76\xa9\x14" + kurz + b"\x88\xac")
    if art == "P2SH-P2WPKH":
        # Die Adresse zeigt nicht auf den Schluessel, sondern auf ein Skript,
        # das seinerseits auf den Schluessel zeigt: OP_0 <20 Byte>. Genau diese
        # Verschachtelung machte SegWit 2017 fuer alte Wallets bezahlbar.
        skripthash = hash160(b"\x00\x14" + kurz)
        return (base58check(bytes((schluessel["_p2sh"],)) + skripthash),
                b"\xa9\x14" + skripthash + b"\x87")
    return bech32(schluessel["_vorsatz"], 0, kurz), b"\x00\x14" + kurz


def zerlegen(text):
    """Einen erweiterten Schluessel in seine sieben Bestandteile zerlegen.

    78 Byte: 4 Version, 1 Tiefe, 4 Fingerabdruck der Eltern, 4 Kindnummer,
    32 Kettencode, 33 Punkt.
    """
    t = (text or "").strip()
    if not t:
        raise UngueltigerSchluessel("form")
    try:
        roh = adresse._base58check(t)
    except adresse.UnbekannteAdresse:
        raise UngueltigerSchluessel("form")
    if len(roh) != 78:
        raise UngueltigerSchluessel("form")

    version = int.from_bytes(roh[:4], "big")
    schluesselteil = roh[45:78]

    # ⚠️ ZUERST auf privat pruefen, und zwar am Datenbyte, nicht an der
    # Versionsliste: ein privater erweiterter Schluessel traegt an Stelle 45 eine
    # Null als Fuellbyte. Das gilt fuer JEDE Version, auch fuer eine, die diese
    # Tabelle nicht kennt. Wer versehentlich seinen xprv einfuegt, soll eine
    # Warnung sehen - und der Schluessel darf nirgends wieder auftauchen.
    if schluesselteil[0] == 0x00:
        raise UngueltigerSchluessel("privat")
    if version in MEHRSIG:
        raise UngueltigerSchluessel("mehrsig", MEHRSIG[version])
    if version not in ARTEN:
        raise UngueltigerSchluessel("version", "%08x" % version)

    name, skriptart, netz, p2pkh, p2sh, vorsatz = ARTEN[version]
    punkt = punkt_aus_bytes(schluesselteil)
    kindnummer = int.from_bytes(roh[9:13], "big")
    return {
        "art": name,
        "skriptart": skriptart,
        "netz": netz,
        "tiefe": roh[4],
        "eltern": roh[5:9].hex(),
        "kindnummer": kindnummer & 0x7FFFFFFF,
        "kind_gehaertet": bool(kindnummer & GEHAERTET),
        "kettencode": roh[13:45],
        "punkt": punkt,
        "fingerabdruck": fingerabdruck(punkt).hex(),
        "_p2pkh": p2pkh, "_p2sh": p2sh, "_vorsatz": vorsatz,
    }


def serialisieren(version, tiefe, eltern, kindnummer, kettencode, punkt):
    """Die Gegenrichtung zu zerlegen(). Braucht die App nicht - der Selbsttest
    braucht sie, um gegen die xpub-Zeichenketten aus BIP32 zu vergleichen."""
    roh = (version.to_bytes(4, "big") + bytes((tiefe,)) + eltern
           + kindnummer.to_bytes(4, "big") + kettencode + bytes_aus_punkt(punkt))
    return base58check(roh)


# --------------------------------------------------------------- Electrum
class _Index:
    """Mehrere offene Verbindungen zum Electrum-Server, fuer die Dauer eines Scans.

    Warum mehrere: das Protokoll ist streng zeilenweise - eine Frage, eine
    Antwort. 240 Adressen nacheinander waeren 240 Wartezeiten hintereinander.
    Vier Straenge teilen sich die Liste; die Wartezeit sinkt entsprechend.

    Warum ueberhaupt eine eigene Klasse und nicht elektrum.adress_uebersicht():
    jene Funktion oeffnet je Adresse eine neue Verbindung samt Handshake. Bei
    einer einzelnen Adresse ist das richtig, bei 240 waere es Unfug.
    """

    AUSFAELLE = (OSError, asyncio.TimeoutError, ValueError, TypeError,
                 elektrum.ElektrumFehler)

    # ⚠️ Zwei Zeitlimits, und der Unterschied ist keine Feinheit: ein NICHT
    # eingerichteter oder abgeschalteter Electrum-Server laesst den Verbindungs-
    # aufbau ins Leere laufen. Mit dem Abfragelimit von 20 s wuerde die Seite
    # dann eine halbe Minute weiss bleiben, bevor sie ehrlich "kein Index" sagt.
    # Also: kurz warten auf das Zustandekommen, lange auf eine Antwort - eine
    # Adresse mit 65.000 Bewegungen braucht am Server gemessen rund 7 s.
    VERBINDEN = 5.0
    ANTWORTEN = 20.0

    def __init__(self, straenge=STRAENGE):
        self.straenge = straenge
        self.verbindungen = []
        self.da = False

    async def __aenter__(self):
        host, port = elektrum.ziel()
        if not host or not port:
            return self
        for _ in range(self.straenge):
            v = elektrum.Verbindung(host, port, self.VERBINDEN)
            try:
                await v.__aenter__()
            except self.AUSFAELLE:
                break
            v.zeitlimit = self.ANTWORTEN
            self.verbindungen.append(v)
        self.da = bool(self.verbindungen)
        return self

    async def __aexit__(self, *_):
        for v in self.verbindungen:
            await v.__aexit__()

    async def _eine(self, v, methode, kennung):
        """Ein Aufruf. None heisst Ausfall - und Ausfall heisst spaeter Strich."""
        try:
            return await v.frage(methode, [kennung])
        except self.AUSFAELLE:
            return None

    async def _verteilen(self, methode, kennungen):
        """Die Liste auf die Straenge aufteilen, Reihenfolge bleibt erhalten."""
        if not self.da or not kennungen:
            return [None] * len(kennungen)
        ergebnis = [None] * len(kennungen)

        async def strang(v, indizes):
            for i in indizes:
                ergebnis[i] = await self._eine(v, methode, kennungen[i])

        anzahl = len(self.verbindungen)
        await asyncio.gather(*[
            strang(v, range(s, len(kennungen), anzahl))
            for s, v in enumerate(self.verbindungen)])
        return ergebnis

    async def historien(self, kennungen):
        return await self._verteilen("blockchain.scripthash.get_history", kennungen)

    async def salden(self, kennungen):
        return await self._verteilen("blockchain.scripthash.get_balance", kennungen)


# --------------------------------------------------------------- Scan
def _adressen_bauen(schluessel, kettenpunkt, kettencode, kette, von, bis):
    """Die Adressen kette/von .. kette/bis-1. Reine Rechnung, kein Netz."""
    raus = []
    for i in range(von, bis):
        kind = kind_ableiten(kettenpunkt, kettencode, i)
        if kind is None:
            # Toter Index (siehe kind_ableiten). Er wird uebersprungen, aber
            # nicht verschwiegen - sonst waere die Nummerierung stillschweigend
            # verschoben.
            continue
        adr, skript = adresse_und_skript(kind[0], schluessel)
        # Gegenprobe mit dem Zerleger aus adresse.py: er liest die eben gebaute
        # Adresse und muss auf genau dasselbe Skript kommen. Damit kann eine
        # falsch geschriebene Adresse nicht neben einem richtigen Saldo stehen -
        # sie fliegt vorher auf. (Nur Mainnet: adresse.py weist fremde Netze
        # ausdruecklich ab, und fuer die fragen wir ohnehin keinen Index.)
        if schluessel["netz"] == "main" and adresse.script_von_adresse(adr)[0] != skript:
            raise UngueltigerSchluessel("adressbau")
        raus.append({"i": i, "pfad": "%d/%d" % (kette, i), "adresse": adr,
                     "art": schluessel["skriptart"],
                     "kennung": hashlib.sha256(skript).digest()[::-1].hex(),
                     "saldo_sat": None, "offen_sat": 0, "anzahl": None,
                     "letzte_hoehe": None, "benutzt": False, "anteil": 0.0})
    return raus


def _historie_deuten(eintrag, verlauf):
    """Was eine Historie ueber eine Adresse verraet. Nichts wird geschaetzt."""
    verlauf = verlauf or []
    eintrag["anzahl"] = len(verlauf)
    eintrag["benutzt"] = len(verlauf) > 0
    hoehen = [e.get("height") or 0 for e in verlauf]
    bestaetigt = [h for h in hoehen if h > 0]
    eintrag["letzte_hoehe"] = max(bestaetigt) if bestaetigt else None
    if not verlauf:
        # Keine Historie heisst zwingend Saldo null. Das ist keine Annahme,
        # sondern Definition - und spart die Haelfte aller Abfragen.
        eintrag["saldo_sat"] = 0


async def _kette_scannen(index, schluessel, kette, luecke, hoechstens):
    """Eine der beiden Ketten bis zur Luecke ablaufen."""
    kind = kind_ableiten(schluessel["punkt"], schluessel["kettencode"], kette)
    if kind is None:
        # Toter Zweig (1 zu 2^127, siehe kind_ableiten). Ueber _kette_zusammen-
        # fassen und nicht als selbstgebautes Woerterbuch, damit die Vorlage
        # jedes Feld vorfindet - ein fehlender Schluessel waere hier ein
        # Serverfehler an der unwahrscheinlichsten Stelle der App.
        return _kette_zusammenfassen(kette, [], False, False)
    kettenpunkt, kettencode = kind

    # ⚠️ `naechster` wird mitgefuehrt und NICHT aus len(alle) abgeleitet: ein
    # uebersprungener toter Index (siehe _adressen_bauen) wuerde die Liste sonst
    # kuerzer machen als die Zaehlung und die naechste Runde beim schon
    # abgeleiteten Index wieder anfangen lassen - dieselbe Adresse zweimal.
    alle, naechster, unvollstaendig = [], 0, False
    while naechster < hoechstens:
        bis = min(naechster + RUNDE, hoechstens)
        neue = _adressen_bauen(schluessel, kettenpunkt, kettencode, kette,
                               naechster, bis)
        naechster = bis
        if not neue:
            continue

        # Erst die Historien: sie sagen, ob eine Adresse je benutzt wurde - und
        # GENAU das entscheidet ueber die Luecke. Am Saldo darf man das nicht
        # festmachen: eine leer geraeumte Adresse hat Saldo null und ist
        # trotzdem benutzt. Wer hier den Saldo nimmt, bricht den Scan bei jeder
        # abgeraeumten Wallet zu frueh ab und zeigt zu wenig Geld.
        verlaeufe = await index.historien([a["kennung"] for a in neue])
        for eintrag, verlauf in zip(neue, verlaeufe):
            if verlauf is None:
                unvollstaendig = True
            else:
                _historie_deuten(eintrag, verlauf)

        offen = [a for a in neue if a["benutzt"]]
        if offen:
            salden = await index.salden([a["kennung"] for a in offen])
            for eintrag, saldo in zip(offen, salden):
                if saldo is None:
                    unvollstaendig = True
                    continue
                eintrag["saldo_sat"] = saldo.get("confirmed", 0)
                eintrag["offen_sat"] = saldo.get("unconfirmed", 0)

        alle.extend(neue)

        # Faellt auch nur eine Abfrage aus, wissen wir nicht, ob die letzten
        # Adressen wirklich leer sind - dann darf die Luecke nicht als erreicht
        # gelten. Wir hoeren trotzdem auf und sagen es.
        if unvollstaendig or _schwanz(alle) >= luecke:
            break

    # "Abgeschnitten" heisst: der Deckel hat uns gestoppt, nicht die Luecke. Die
    # Wallet kann weitergehen, wir sehen es nur nicht mehr - etwas ganz anderes
    # als "fertig gelesen".
    abgeschnitten = (not unvollstaendig and naechster >= hoechstens
                     and _schwanz(alle) < luecke)
    return _kette_zusammenfassen(kette, alle, unvollstaendig, abgeschnitten)


def _schwanz(alle):
    """Wie viele Adressen am Ende hintereinander nie benutzt wurden."""
    n = 0
    for eintrag in reversed(alle):
        if eintrag["benutzt"]:
            break
        n += 1
    return n


def _kette_zusammenfassen(kette, alle, unvollstaendig, abgeschnitten):
    benutzt = [a for a in alle if a["benutzt"]]
    salden = [a["saldo_sat"] for a in benutzt if a["saldo_sat"] is not None]
    hoechster = max(salden) if salden else 0
    for eintrag in alle:
        s = eintrag["saldo_sat"]
        # Wurzel statt linear - wie im Aktivitaetsband der Adressseite: eine
        # einzelne grosse Zahlung wuerde sonst alle anderen Saeulen auf eine
        # Nulllinie druecken, und gerade die Verteilung ist hier die Aussage.
        eintrag["anteil"] = round((s / hoechster) ** 0.5, 4) if (s and hoechster) else 0.0
    # ⚠️ Fiel etwas aus und wissen wir KEINEN einzigen Saldo, dann ist die Summe
    # nicht null, sondern unbekannt. Eine Null waere hier die gefaehrlichste
    # Zahl der ganzen App: sie liest sich als "leere Wallet".
    if unvollstaendig and not salden:
        summe = None
    else:
        summe = sum(salden)
    return {
        "nr": kette,
        "adressen": alle,
        "benutzte": benutzt,
        "geprueft": len(alle),
        "benutzt": len(benutzt),
        "saldo_sat": summe,
        "offen_sat": sum(a["offen_sat"] or 0 for a in benutzt),
        "bewegungen": sum(a["anzahl"] or 0 for a in benutzt),
        "abgeschnitten": abgeschnitten,
        "unvollstaendig": unvollstaendig,
        "leer": not benutzt,
    }


# --------------------------------------------------------------- Hauptweg
def _kurz(text, vorn=12, hinten=8):
    """Ein langer Schluessel, so gekuerzt, dass man ihn noch wiedererkennt."""
    t = (text or "").strip()
    if len(t) <= vorn + hinten + 1:
        return t
    return t[:vorn] + "…" + t[-hinten:]


def _leer(grund, zusatz=None, sprache=STANDARD):
    from .blockseite import btc_text
    return {"gefunden": False, "grund": grund, "zusatz": zusatz,
            "ketten": [], "btc": lambda sat: btc_text(sat, sprache)}


async def uebersicht(schluesseltext, sprache=STANDARD, luecke=LUECKE,
                     hoechstens=HOECHSTENS):
    """Alles, was xpub.html braucht - oder ein sauberes "nicht verwendbar".

    ⚠️ Der Schluesseltext selbst steht ABSICHTLICH nicht vollstaendig im
    Rueckgabewert; die Seite zeigt nur die gekuerzte Form. Bei einem privaten
    Schluessel taucht er nirgends wieder auf, auch nicht gekuerzt.
    """
    from .blockseite import btc_text

    try:
        s = zerlegen(schluesseltext)
    except UngueltigerSchluessel as f:
        return _leer(f.grund, f.zusatz, sprache)

    # Ein Schluessel eines fremden Netzes gegen einen Mainnet-Index zu halten
    # wuerde ueberall saubere Nullen liefern - und die waeren gelogen. Die
    # Adressen stimmen trotzdem, also zeigen wir sie und lassen die Salden weg.
    testnetz = s["netz"] != "main"

    index_da = False
    try:
        if testnetz:
            ketten = [_kette_trocken(s, k, luecke) for k in (EMPFANG, WECHSEL)]
        else:
            async with _Index() as index:
                index_da = index.da
                if index_da:
                    # Nacheinander, nicht nebenlaeufig: beide Ketten teilen sich
                    # dieselben vier Verbindungen, und die Wechselgeldkette ist
                    # im Regelfall nach einer Runde durch.
                    ketten = [await _kette_scannen(index, s, k, luecke, hoechstens)
                              for k in (EMPFANG, WECHSEL)]
                else:
                    # Kein Index eingerichtet oder nicht erreichbar: die
                    # Adressen stimmen trotzdem, nur ueber ihr Geld wissen wir
                    # nichts.
                    ketten = [_kette_trocken(s, k, luecke)
                              for k in (EMPFANG, WECHSEL)]
    except UngueltigerSchluessel as f:
        # Hierher kommt nur, was gar nicht vorkommen darf: eine Adresse, die
        # sich nicht wieder einlesen laesst, oder ein Punkt, der nicht auf der
        # Kurve liegt. Dann zeigt die Seite gar nichts - lieber nichts als
        # Adressen, an die jemand Geld schickt.
        return _leer(f.grund, f.zusatz, sprache)

    salden = [k["saldo_sat"] for k in ketten if k["saldo_sat"] is not None]
    unvollstaendig = any(k["unvollstaendig"] for k in ketten)
    if salden:
        gesamt = sum(salden)
    elif index_da and not unvollstaendig:
        gesamt = 0
    else:
        gesamt = None
    return {
        "gefunden": True,
        "grund": None,
        "btc": lambda sat: btc_text(sat, sprache),
        "kurz": _kurz(schluesseltext),
        "art": s["art"],
        "skriptart": s["skriptart"],
        "netz": s["netz"],
        "testnetz": testnetz,
        "tiefe": s["tiefe"],
        "eltern": s["eltern"],
        "fingerabdruck": s["fingerabdruck"],
        "kindnummer": s["kindnummer"],
        "kind_gehaertet": s["kind_gehaertet"],
        "ketten": ketten,
        "gesamt_sat": gesamt,
        "offen_sat": sum(k["offen_sat"] for k in ketten),
        "benutzt": sum(k["benutzt"] for k in ketten),
        "geprueft": sum(k["geprueft"] for k in ketten),
        "bewegungen": sum(k["bewegungen"] for k in ketten),
        "leer": all(k["leer"] for k in ketten),
        "index_da": index_da,
        "unvollstaendig": unvollstaendig,
        "abgeschnitten": any(k["abgeschnitten"] for k in ketten),
        "luecke": luecke,
    }


def _kette_trocken(schluessel, kette, luecke):
    """Nur ableiten, nicht fragen - ohne Index und fuer fremde Netze."""
    kind = kind_ableiten(schluessel["punkt"], schluessel["kettencode"], kette)
    if kind is None:
        return _kette_zusammenfassen(kette, [], False, False)
    alle = _adressen_bauen(schluessel, kind[0], kind[1], kette, 0, luecke)
    for eintrag in alle:
        # Kein Index gefragt, also ist NICHTS bekannt - weder Saldo noch
        # Bewegungen. Strich, nicht Null.
        eintrag["saldo_sat"] = None
    zusammen = _kette_zusammenfassen(kette, alle, False, False)
    zusammen["adressen"] = alle
    zusammen["benutzte"] = alle          # ohne Index zeigen wir schlicht alle
    zusammen["saldo_sat"] = None
    zusammen["leer"] = False
    return zusammen


# --------------------------------------------------------------- Beleg
# Ab hier wird nur noch geprueft. Der Teil gehoert ausdruecklich IN diese Datei
# und nicht in ein Testverzeichnis: eine falsche Ableitung liefert lauter
# wohlgeformte, aber fremde Adressen - ohne mitgelieferten Beleg ist dieses
# Modul nicht pruefbar, sondern nur glaubhaft. Kein Netz, keine Abhaengigkeit:
#     python3 -m satscope.xpub
_XPUB_VERSION = 0x0488B21E

# BIP32, Testvektoren 1 und 2. Jede Zeile ist ein NICHT gehaerteter Schritt -
# genau der Schritt, den diese Seite geht. (Eltern, Index, Kind)
_BIP32 = (
    ("xpub68Gmy5EdvgibQVfPdqkBBCHxA5htiqg55crXYuXoQRKfDBFA1WEjWgP6LHhwBZeNK1VTsfTFUHCdrfp1bgwQ9xv5ski8PX9rL2dZXvgGDnw",
     1,
     "xpub6ASuArnXKPbfEwhqN6e3mwBcDTgzisQN1wXN9BJcM47sSikHjJf3UFHKkNAWbWMiGj7Wf5uMash7SyYq527Hqck2AxYysAA7xmALppuCkwQ"),
    ("xpub6D4BDPcP2GT577Vvch3R8wDkScZWzQzMMUm3PWbmWvVJrZwQY4VUNgqFJPMM3No2dFDFGTsxxpG5uJh7n7epu4trkrX7x7DogT5Uv6fcLW5",
     2,
     "xpub6FHa3pjLCk84BayeJxFW2SP4XRrFd1JYnxeLeU8EqN3vDfZmbqBqaGJAyiLjTAwm6ZLRQUMv1ZACTj37sR62cfN7fe5JnJ7dh8zL4fiyLHV"),
    ("xpub6FHa3pjLCk84BayeJxFW2SP4XRrFd1JYnxeLeU8EqN3vDfZmbqBqaGJAyiLjTAwm6ZLRQUMv1ZACTj37sR62cfN7fe5JnJ7dh8zL4fiyLHV",
     1000000000,
     "xpub6H1LXWLaKsWFhvm6RVpEL9P4KfRZSW7abD2ttkWP3SSQvnyA8FSVqNTEcYFgJS2UaFcxupHiYkro49S8yGasTvXEYBVPamhGW6cFJodrTHy"),
    ("xpub661MyMwAqRbcFW31YEwpkMuc5THy2PSt5bDMsktWQcFF8syAmRUapSCGu8ED9W6oDMSgv6Zz8idoc4a6mr8BDzTJY47LJhkJ8UB7WEGuduB",
     0,
     "xpub69H7F5d8KSRgmmdJg2KhpAK8SR3DjMwAdkxj3ZuxV27CprR9LgpeyGmXUbC6wb7ERfvrnKZjXoUmmDznezpbZb7ap6r1D3tgFxHmwMkQTPH"),
    ("xpub6ASAVgeehLbnwdqV6UKMHVzgqAG8Gr6riv3Fxxpj8ksbH9ebxaEyBLZ85ySDhKiLDBrQSARLq1uNRts8RuJiHjaDMBU4Zn9h8LZNnBC5y4a",
     1,
     "xpub6DF8uhdarytz3FWdA8TvFSvvAh8dP3283MY7p2V4SeE2wyWmG5mg5EwVvmdMVCQcoNJxGoWaU9DCWh89LojfZ537wTfunKau47EL2dhHKon"),
    ("xpub6ERApfZwUNrhLCkDtcHTcxd75RbzS1ed54G1LkBUHQVHQKqhMkhgbmJbZRkrgZw4koxb5JaHWkY4ALHY2grBGRjaDMzQLcgJvLJuZZvRcEL",
     2,
     "xpub6FnCn6nSzZAw5Tw7cgR9bi15UV96gLZhjDstkXXxvCLsUXBGXPdSnLFbdpq8p9HmGsApME5hQTZ3emM2rnY5agb9rXpVGyy3bdW6EEgAtqt"),
)

# BIP84, Konto 0 der Wallet "abandon ... about": (Kette, Index, Adresse).
_BIP84_ZPUB = ("zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1A"
               "DqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs")
_BIP84 = ((0, 0, "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"),
          (0, 1, "bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g"),
          (1, 0, "bc1q8c6fshw2dlwun7ekn9qwf37cu2rn755upcp6el"))

# BIP49, Konto 0 im Testnetz - der einzige veroeffentlichte Vektor fuer den
# verschachtelten Weg (P2SH-P2WPKH) ueber einen ganzen erweiterten Schluessel.
_BIP49_UPUB = ("upub5EFU65HtV5TeiSHmZZm7FUffBGy8UKeqp7vw43jYbvZPpoVsgU93oac7Wk3u6"
               "moKegAEWtGNF8DehrnHtv21XXEMYRUocHqguyjknFHYfgY")
_BIP49_ADRESSE = "2Mww8dCYPUpKHofjgcXcBCEGmniw9CoaiD2"

# RIPEMD-160, Vektoren der Urheber (Dobbertin, Bosselaers, Preneel).
_RIPEMD_VEKTOREN = (
    (b"", "9c1185a5c5e9fc54612808977ee8f548b2258d31"),
    (b"abc", "8eb208f7e05d987a9b044a8e98c6b087f15a0bfc"),
    (b"message digest", "5d0689ef49d2fae572b881b123a85ffa21595f36"),
    (b"abcdefghijklmnopqrstuvwxyz", "f71c27109c692c1b56bbdceb5b9d2865b3708dbc"),
    (b"1234567890" * 8, "9b752e45573d4b39f4dbd3323cab82bf63326bfb"),
)


def selbsttest():
    """Alle Rechenwege gegen veroeffentlichte Vektoren. 0 = alles in Ordnung."""
    fehler = []

    def pruefe(name, ist, soll):
        if ist != soll:
            fehler.append("%s: %r erwartet, %r bekommen" % (name, soll, ist))
            print("  FEHLER  %s" % name)
        else:
            print("  ok      %s" % name)

    print("RIPEMD-160 in reinem Python (falls OpenSSL keins fuehrt)")
    for daten, soll in _RIPEMD_VEKTOREN:
        pruefe("ripemd160(%r)" % daten[:14], ripemd160_rein(daten).hex(), soll)
    pruefe("beide Wege gleich", _RIPEMD(b"satscope"),
           ripemd160_rein(b"satscope"))

    print("\nBIP32: nicht gehaertete Ableitung aus dem oeffentlichen Schluessel")
    for eltern_text, index, soll in _BIP32:
        e = zerlegen(eltern_text)
        kindnummer = e["kindnummer"] | (GEHAERTET if e["kind_gehaertet"] else 0)
        # Erst zurueckschreiben: was wir gelesen haben, muss sich unveraendert
        # wieder zusammensetzen lassen - sonst prueft der Vergleich unten nur
        # zwei gleich falsche Rechnungen gegeneinander.
        pruefe("Eltern %s… unveraendert zurueck" % eltern_text[:12],
               serialisieren(_XPUB_VERSION, e["tiefe"], bytes.fromhex(e["eltern"]),
                             kindnummer, e["kettencode"], e["punkt"]), eltern_text)
        kind = kind_ableiten(e["punkt"], e["kettencode"], index)
        pruefe("Kind %d von %s…" % (index, eltern_text[:12]),
               serialisieren(_XPUB_VERSION, e["tiefe"] + 1,
                             fingerabdruck(e["punkt"]), index, kind[1], kind[0]),
               soll)

    print("\nBIP84: zpub -> Adressen (genau der Weg dieser Seite)")
    s = zerlegen(_BIP84_ZPUB)
    pruefe("Art erkannt", (s["art"], s["skriptart"], s["netz"]),
           ("zpub", "P2WPKH", "main"))
    for kette, i, soll in _BIP84:
        kp, kc = kind_ableiten(s["punkt"], s["kettencode"], kette)
        pruefe("m/84'/0'/0'/%d/%d" % (kette, i),
               _adressen_bauen(s, kp, kc, kette, i, i + 1)[0]["adresse"], soll)

    print("\nBIP49: upub -> verschachteltes SegWit")
    u = zerlegen(_BIP49_UPUB)
    pruefe("Art erkannt", (u["art"], u["skriptart"], u["netz"]),
           ("upub", "P2SH-P2WPKH", "test"))
    kp, kc = kind_ableiten(u["punkt"], u["kettencode"], 0)
    punkt = kind_ableiten(kp, kc, 0)[0]
    pruefe("Punkt m/49'/1'/0'/0/0", bytes_aus_punkt(punkt).hex(),
           "03a1af804ac108a8a51782198c2d034b28bf90c8803f5a53f76276fa69a4eae77f")
    pruefe("HASH160 des Punktes", hash160(bytes_aus_punkt(punkt)).hex(),
           "38971f73930f6c141d977ac4fd4a727c854935b3")
    pruefe("HASH160 des Einloeseskripts",
           hash160(b"\x00\x14" + hash160(bytes_aus_punkt(punkt))).hex(),
           "336caa13e08b96080a32b5d818d59b4ab3b36742")
    pruefe("Adresse m/49'/1'/0'/0/0",
           _adressen_bauen(u, kp, kc, 0, 0, 1)[0]["adresse"], _BIP49_ADRESSE)

    print("\nBase58: Zusammensetzen gegen den Zerleger aus adresse.py")
    for n in range(5):
        # Fuehrende Nullbyte sind die klassische Falle: sie verschwinden in der
        # Zahl und muessen als "1" nachgezaehlt werden.
        nutz = b"\x00" * n + bytes(range(1, 22 - n))
        pruefe("%d fuehrende Nullbyte hin und zurueck" % n,
               adresse._base58check(base58check(nutz)), nutz)

    print("\nAbwehr")
    for name, text, grund in (
            ("privater Schluessel",
             "xprv9s21ZrQH143K3QTDL4LXw2F7HEK3wJUD2nW2nRk4stbPy6cq3jPPqjiChkVvv"
             "NKmPGJxWUtg6LnF5kejMRNNU3TGtRBeJgk33yuGBxrMPHi", "privat"),
            ("ein Zeichen verdreht", _BIP84_ZPUB[:-1] + "t", "form"),
            ("eine Adresse", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "form"),
            ("nichts", "", "form")):
        try:
            zerlegen(text)
            pruefe(name, "durchgelassen", grund)
        except UngueltigerSchluessel as f:
            pruefe(name, f.grund, grund)
    try:
        kind_ableiten(s["punkt"], s["kettencode"], GEHAERTET)
        pruefe("gehaerteter Index", "durchgelassen", "abgewiesen")
    except UngueltigerSchluessel as f:
        pruefe("gehaerteter Index", f.grund, "gehaertet")

    print("\n%d Fehler" % len(fehler))
    for f in fehler:
        print("  " + f)
    return 1 if fehler else 0


if __name__ == "__main__":
    import sys
    sys.exit(selbsttest())
