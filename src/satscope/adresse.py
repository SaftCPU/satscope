"""Bitcoin-Adressen zerlegen und in Electrums Skript-Kennung uebersetzen.

Bewusst ohne Fremdbibliothek: Base58 und Bech32 sind wenige Zeilen, und jede
Abhaengigkeit weniger ist ein Angriffsweg weniger in einer App, die neben einem
Bitcoin-Knoten laeuft.

Electrum adressiert nicht ueber Adressen, sondern ueber den SHA-256 des
scriptPubKey, byteweise umgedreht (BIP-Konvention "scripthash"). Diese
Uebersetzung ist der ganze Zweck dieses Moduls.

Umgesetzt sind alle heute gebraeuchlichen Formen:
    1...      P2PKH    Base58Check, Version 0x00
    3...      P2SH     Base58Check, Version 0x05
    bc1q...   P2WPKH/P2WSH   Bech32,  Zeuge Fassung 0
    bc1p...   P2TR     Bech32m, Zeuge Fassung 1
"""
import hashlib

# 58 Zeichen: ohne 0, O, I und l - die sind paarweise verwechselbar.
# ⚠️ Ein einziges Zeichen zu viel verschiebt ALLE Stellenwerte dahinter; der
# erste Entwurf hatte das I drin (59 Zeichen) und lieferte fuer die
# Genesis-Adresse eine falsche Pruefsumme.
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
assert len(B58) == 58
BECH = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
BECH32M = 0x2BC830A3


class UnbekannteAdresse(ValueError):
    """Keine erkennbare Bitcoin-Adresse."""


# --------------------------------------------------------------- Base58
def _base58check(a):
    zahl = 0
    for z in a:
        if z not in B58:
            raise UnbekannteAdresse("ungueltiges Zeichen")
        zahl = zahl * 58 + B58.index(z)
    roh = zahl.to_bytes((zahl.bit_length() + 7) // 8, "big")
    # Fuehrende Einsen sind fuehrende Nullbytes.
    roh = b"\x00" * (len(a) - len(a.lstrip("1"))) + roh
    if len(roh) < 5:
        raise UnbekannteAdresse("zu kurz")
    nutz, pruef = roh[:-4], roh[-4:]
    if hashlib.sha256(hashlib.sha256(nutz).digest()).digest()[:4] != pruef:
        raise UnbekannteAdresse("Pruefsumme stimmt nicht")
    return nutz


# --------------------------------------------------------------- Bech32
def _polymod(werte):
    G = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    p = 1
    for w in werte:
        oben = p >> 25
        p = ((p & 0x1FFFFFF) << 5) ^ w
        for i in range(5):
            if (oben >> i) & 1:
                p ^= G[i]
    return p


def _erweitere(vorsatz):
    return [ord(z) >> 5 for z in vorsatz] + [0] + [ord(z) & 31 for z in vorsatz]


def _bech32_zerlegen(a):
    if a != a.lower() and a != a.upper():
        raise UnbekannteAdresse("gemischte Gross- und Kleinschreibung")
    a = a.lower()
    trenn = a.rfind("1")
    if trenn < 1 or trenn + 7 > len(a) or len(a) > 90:
        raise UnbekannteAdresse("keine gueltige Bech32-Form")
    vorsatz, daten = a[:trenn], a[trenn + 1:]
    if any(z not in BECH for z in daten):
        raise UnbekannteAdresse("ungueltiges Zeichen")
    werte = [BECH.index(z) for z in daten]
    pruef = _polymod(_erweitere(vorsatz) + werte)
    if pruef == 1:
        art = "bech32"
    elif pruef == BECH32M:
        art = "bech32m"
    else:
        raise UnbekannteAdresse("Pruefsumme stimmt nicht")
    return vorsatz, werte[:-6], art


def _fuenf_auf_acht(werte):
    speicher, bits, raus = 0, 0, bytearray()
    for w in werte:
        speicher = (speicher << 5) | w
        bits += 5
        while bits >= 8:
            bits -= 8
            raus.append((speicher >> bits) & 0xFF)
    if bits >= 5 or ((speicher << (8 - bits)) & 0xFF):
        raise UnbekannteAdresse("ungueltige Fuellbits")
    return bytes(raus)


# --------------------------------------------------------------- oeffentlich
def script_von_adresse(a):
    """(scriptPubKey, Bezeichnung der Adressart)."""
    a = (a or "").strip()
    if not a:
        raise UnbekannteAdresse("leer")

    if a[0] in "13":
        nutz = _base58check(a)
        version, rumpf = nutz[0], nutz[1:]
        if len(rumpf) != 20:
            raise UnbekannteAdresse("falsche Laenge")
        if version == 0x00:                       # P2PKH
            return b"\x76\xa9\x14" + rumpf + b"\x88\xac", "P2PKH"
        if version == 0x05:                       # P2SH
            return b"\xa9\x14" + rumpf + b"\x87", "P2SH"
        raise UnbekannteAdresse("unbekannte Version")

    if a.lower().startswith("bc1"):
        vorsatz, werte, art = _bech32_zerlegen(a)
        if vorsatz != "bc":
            raise UnbekannteAdresse("kein Mainnet")
        # ⚠️ "bc1gmk9yu" hat eine GUELTIGE Bech32-Pruefsumme bei LEERER Nutzlast.
        # Ohne diese Zeile wirft werte[0] einen IndexError statt einer sauberen
        # Ablehnung - und /address/bc1gmk9yu antwortete mit HTTP 500 (gefunden
        # 16.08.2026 beim Bau der Suche). Eine gueltige Pruefsumme heisst eben
        # nur, dass nichts verstuemmelt wurde, nicht dass etwas drinsteht.
        if not werte:
            raise UnbekannteAdresse("leere Nutzlast")
        fassung, rumpf = werte[0], _fuenf_auf_acht(werte[1:])
        if fassung == 0:
            if art != "bech32":
                raise UnbekannteAdresse("Fassung 0 verlangt Bech32")
            if len(rumpf) == 20:
                return b"\x00\x14" + rumpf, "P2WPKH"
            if len(rumpf) == 32:
                return b"\x00\x20" + rumpf, "P2WSH"
            raise UnbekannteAdresse("falsche Laenge")
        if art != "bech32m":
            raise UnbekannteAdresse("Fassung ab 1 verlangt Bech32m")
        if not 2 <= len(rumpf) <= 40:
            raise UnbekannteAdresse("falsche Laenge")
        vorop = 0x50 + fassung                    # OP_1 .. OP_16
        name = "P2TR" if fassung == 1 else "Zeuge v%d" % fassung
        return bytes([vorop, len(rumpf)]) + rumpf, name

    raise UnbekannteAdresse("unbekannte Form")


def scripthash(a):
    """Electrums Kennung: SHA-256 des Skripts, byteweise umgedreht."""
    skript, art = script_von_adresse(a)
    return hashlib.sha256(skript).digest()[::-1].hex(), art
