"""Ein einziger Satz: wie steht das Netz gerade?

Die Entwurfsidee in ihrer reinsten Form. Ein Armaturenbrett zeigt Zahlen und
laesst den Leser deuten - hier steht die Deutung selbst, und die Zahlen stehen
darunter als Beleg.

REGELN, die diesen Satz erst brauchbar machen:

* Jede Aussage muss aus den erhobenen Werten BELEGBAR sein. Keine Schaetzung,
  keine Stimmung, kein "gerade ist viel los" ohne Zahl dahinter.
* Es wird genau EIN Satz gewaehlt - der spezifischste, der zutrifft. Drei
  gleichzeitige Hinweise waeren wieder ein Armaturenbrett.
* Faellt eine Quelle aus, gibt es keinen Satz. Lieber nichts sagen als etwas
  Ungedecktes.

Die Reihenfolge unten ist die Rangfolge: was zuerst passt, gewinnt. Ganz oben
steht, was den Nutzer bei seiner naechsten Entscheidung wirklich betrifft.
"""

# Ein Block fasst rund 1.000.000 virtuelle Byte. getmempoolinfo["bytes"] ist die
# Summe der virtuellen Groessen - damit ist "passt alles in den naechsten Block"
# eine nachrechenbare Aussage und keine Redensart.
BLOCK_VBYTE = 1000000


def _bloecke_im_mempool(bytes_):
    return bytes_ / BLOCK_VBYTE if bytes_ else 0.0


def lage(z, t):
    """(Satz, Art) oder (None, None). `art` faerbt den Hinweis in der Vorlage."""
    if not z or not z.get("erreichbar"):
        return None, None

    hoehe = z.get("hoehe")
    alter = z.get("block_alter")
    mem = z.get("mempool_bytes")
    rueckstand = z.get("index_rueckstand")

    # 1. Der Index haengt zurueck. Ganz nach oben, weil es JEDE Adressabfrage
    #    still verfaelscht - der Nutzer saehe sonst zu wenig und wuesste nicht,
    #    warum. Ab zwei Bloecken, damit der normale Versatz beim Blockfund
    #    keinen Alarm ausloest.
    if isinstance(rueckstand, int) and rueckstand >= 2:
        return t.t("lage.index_haengt", n=t.zahl(rueckstand)), "warnung"

    # 2. Lange kein Block. Bis drei Stunden ist das bei Bitcoin normal, aber
    #    ab einer Stunde erklaert es, warum sich gerade nichts bewegt.
    if isinstance(alter, int) and alter >= 3600:
        return t.t("lage.kein_block", n=t.zahl(alter // 60)), "warnung"

    # 3. Der Mempool passt in einen Block. Dann ist jede Gebuehr, die ueber der
    #    Weiterleitungsgrenze liegt, fuer den naechsten Block genug - das ist
    #    die nuetzlichste Aussage ueberhaupt und gilt nur selten.
    if isinstance(mem, int) and 0 < mem < BLOCK_VBYTE:
        return t.t("lage.leer"), "gut"

    # 4. Es staut sich. Ab acht Blocklaengen wird das Warten spuerbar.
    if isinstance(mem, int) and mem >= 8 * BLOCK_VBYTE:
        return t.t("lage.stau",
                   mb=t.zahl(mem / 1000000, 1),
                   n=t.zahl(round(_bloecke_im_mempool(mem)))), "warnung"

    # 5. Gerade eben ein Block. Kurzlebig, deshalb weit unten - aber es erklaert,
    #    warum die Gebuehrenschaetzung in diesem Moment besonders niedrig ist.
    if isinstance(alter, int) and alter < 120 and hoehe:
        return t.t("lage.frisch", h=t.zahl(hoehe)), "gut"

    # 6. Der Normalfall. Auch er bekommt eine Einordnung, keine nackte Zahl.
    if isinstance(mem, int) and mem > 0:
        return t.t("lage.normal",
                   mb=t.zahl(mem / 1000000, 1),
                   n=t.zahl(round(_bloecke_im_mempool(mem)))), "normal"

    return None, None
