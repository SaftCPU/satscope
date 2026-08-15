"""Die Mempool-Tiefenkarte: wo landet meine Gebuehr?

Kein Text, kein Diagrammwerkzeug - eine handgezeichnete SVG-Treppe. Waagerecht
der Platz in Bloecken, senkrecht die Gebuehr. Wer wissen will, ob seine Gebuehr
in den naechsten Block kommt, sucht ihre Hoehe und liest ab, ob sie links oder
rechts der ersten Blockgrenze liegt.

QUELLE: mempool.get_fee_histogram vom Electrum-Server - gemessen **5 ms**.
Bitcoin Core koennte dasselbe nur ueber getrawmempool true liefern: 350-450 ms
und 13 MB JSON, im Webprozess ausdruecklich verboten.

Das Histogramm kommt als [[Gebuehr, vsize], ...], nach Gebuehr absteigend.
Genau die Reihenfolge, in der ein Miner einbaut - deshalb ist die kumulierte
Summe unmittelbar der Platz, der vor einer Gebuehr liegt.
"""
import asyncio

from .elektrum import ElektrumFehler, Verbindung, ziel

BLOCK_VBYTE = 1000000
# Der sichtbare Bereich passt sich den Daten an. Fest auf vier Bloecke
# gerechnet stand bei leerem Mempool zwei Drittel der Flaeche leer, bei Stau
# waere umgekehrt alles zusammengequetscht. Mindestens 1,5 Bloecke, damit die
# erste Blockgrenze - die wichtigste Linie der Karte - immer im Bild ist.
SICHTBAR_MIN, SICHTBAR_MAX = 1.5, 8.0
BREITE, HOEHE = 1000.0, 260.0
RAND_L, RAND_R, RAND_O, RAND_U = 6.0, 6.0, 14.0, 26.0


async def histogramm(zeitlimit=6.0):
    host, port = ziel()
    if not host or not port:
        return None
    try:
        async with Verbindung(host, port, zeitlimit) as v:
            return await v.frage("mempool.get_fee_histogram")
    except (OSError, asyncio.TimeoutError, ValueError, TypeError,
            ElektrumFehler):
        return None


def _farbe(satz):
    """Gebuehr auf einen Farbton. Guenstig blaeulich, teuer roetlich -
    derselbe Verlauf wie bei den Bloecken, damit man ihn einmal lernt."""
    if satz <= 0:
        return "#544b73"   # heller, sonst verschwindet der Schwanz im Grund
    for grenze, ton in ((1, "#4f7fd8"), (2, "#5a9bd4"), (4, "#63b8a8"),
                        (8, "#9fc46a"), (20, "#e0b055"), (60, "#e08a4a")):
        if satz < grenze:
            return ton
    return "#e0604a"


def karte(hist):
    """Fertige Zeichnung als dict fuer die Vorlage. None, wenn keine Daten.

    Gerechnet wird serverseitig, damit die Seite ohne JavaScript vollstaendig
    ist - die Animation kommt allein aus CSS.
    """
    if not hist:
        return None
    stufen = []
    for eintrag in hist:
        try:
            satz, groesse = float(eintrag[0]), float(eintrag[1])
        except (TypeError, ValueError, IndexError):
            continue
        if groesse > 0:
            stufen.append((satz, groesse))
    if not stufen:
        return None
    stufen.sort(key=lambda s: s[0], reverse=True)

    gesamt = sum(g for _, g in stufen)
    hoechste = max(s for s, _ in stufen)
    # Wieviel Platz belegen die Gebuehren ueber null? Der Staubschwanz bei
    # 0 sat/vB ist zwar riesig (gemessen 26,7 von 28,0 MB), aber er wird nie
    # eingebaut - er darf den Massstab nicht bestimmen.
    zahlend = sum(g for s, g in stufen if s > 0) / BLOCK_VBYTE
    sichtbar = max(SICHTBAR_MIN, min(SICHTBAR_MAX, zahlend * 1.35))
    # Senkrecht mit Wurzel skalieren: sonst draengen sich alle Gebuehren unter
    # 3 sat/vB in den untersten Pixeln zusammen, und genau dort wird
    # entschieden. Keine Logarithmen - die machen 0 unmoeglich.
    obergrenze = max(hoechste, 2.0)

    def x_von(bloecke):
        return RAND_L + (min(bloecke, sichtbar) / sichtbar) * (BREITE - RAND_L - RAND_R)

    def y_von(satz):
        anteil = (satz / obergrenze) ** 0.5
        return HOEHE - RAND_U - anteil * (HOEHE - RAND_O - RAND_U)

    balken, kum = [], 0.0
    for satz, groesse in stufen:
        von, kum = kum, kum + groesse / BLOCK_VBYTE
        if von >= sichtbar:
            break
        x1, x2 = x_von(von), x_von(kum)
        if x2 - x1 < 0.6:          # unsichtbar schmal, aber vorhanden
            x2 = x1 + 0.6
        y = y_von(satz)
        # Mindesthoehe: der Staubschwanz bei 0 sat/vB waere sonst UNSICHTBAR,
        # obwohl er 95 % des Mempools ausmacht (gemessen 26,7 von 28,0 MB).
        # Genau seine Breite ist die Aussage - dass er flach ist, sieht man dann.
        mindest = 7.0 if satz <= 0 else 3.0
        if HOEHE - RAND_U - y < mindest:
            y = HOEHE - RAND_U - mindest
        balken.append({
            "x": round(x1, 2), "breite": round(x2 - x1, 2),
            "y": round(y, 2), "hoehe": round(HOEHE - RAND_U - y, 2),
            "farbe": _farbe(satz), "satz": satz,
            "vsize": int(groesse),
        })

    # Blockgrenzen: die erste ist die wichtigste Linie der ganzen Karte.
    grenzen = [{"x": round(x_von(i), 2), "nr": i}
               for i in range(1, int(sichtbar) + 1)]

    return {
        "breite": BREITE, "hoehe": HOEHE,
        "grundlinie": round(HOEHE - RAND_U, 2),
        "balken": balken, "grenzen": grenzen,
        "gesamt_bloecke": round(gesamt / BLOCK_VBYTE, 2),
        "ueberlauf": gesamt / BLOCK_VBYTE > sichtbar,
        "sichtbar": round(sichtbar, 2),
        "hoechste": hoechste,
        # Wo endet der Platz des naechsten Blocks? Der Satz an dieser Stelle
        # ist die eigentliche Aufnahmeschwelle.
        "schwelle": next((s for s, _ in _kumuliert(stufen)
                          if _ >= BLOCK_VBYTE), None),
    }


def _kumuliert(stufen):
    k = 0.0
    for satz, groesse in stufen:
        k += groesse
        yield satz, k
