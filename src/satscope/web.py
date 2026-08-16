"""Der Web-Dienst. Liest nur, schreibt nie.

Bewusst KEIN FastAPI (Pydantic-Ballast ohne Nutzen, wir bauen kein JSON-Produkt)
und kein Flask (kann WebSocket-Push und Hintergrundaufgaben im selben Prozess
nur mit Zusatzmaschinerie).

Der Web-Prozess bekommt AUSSCHLIESSLICH das billige RPC-Tor. rpc_teuer wird hier
absichtlich nicht importiert: gettxoutsetinfo braucht am Knoten gemessene 58 s -
ein unbedachter Handler waere ein Denial-of-Service gegen den eigenen Node.
"""
import asyncio
import os

from . import (adresse, adressdetails, blockliste, blockseite, elektrum,
               gebuehren, kette, knoten, knotenseite, lage, mempoolseite,
               mining, spiel, suchen, tiefenkarte, txseite, xpub)
from .rpc import BILLIG, Tor
from .sprache import COOKIE, COOKIE_ALTER, SPRACHEN, Texte, sprache_aus_cookies

HIER = os.path.dirname(__file__)
TOR = Tor(erlaubt=BILLIG)


def _dauer_text(sekunden, sprache):
    """Erste Darstellung des Blockalters. satscope.js zaehlt sie danach weiter."""
    if sekunden is None:
        return "\u2013"
    minuten = max(0, int(sekunden)) // 60
    if minuten < 1:
        return "gerade eben" if sprache == "de" else "just now"
    if minuten < 60:
        return ("vor %d Min." % minuten) if sprache == "de" else ("%d min ago" % minuten)
    stunden, rest = divmod(minuten, 60)
    if sprache == "de":
        return "vor %d Std.%s" % (stunden, (" %d Min." % rest) if rest else "")
    return "%d h%s ago" % (stunden, (" %d min" % rest) if rest else "")


def _btc(sat):
    """Satoshi als BTC mit acht Stellen, ohne nachlaufende Nullen-Wueste.

    Bewusst NICHT ueber Fliesskomma: 0,1 + 0,2 ist dort nicht 0,3, und bei
    Geldbetraegen faellt so etwas irgendwann auf.
    """
    if sat is None:
        return "\u2013"
    ganz, rest = divmod(int(sat), 100000000)
    return "%d.%08d" % (ganz, rest)


def erzeuge_app():
    from starlette.applications import Starlette
    from starlette.responses import RedirectResponse
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles
    from starlette.templating import Jinja2Templates

    vorlagen = Jinja2Templates(directory=os.path.join(HIER, "vorlagen"))

    async def startseite(request):
        t = Texte(sprache_aus_cookies(request.cookies))
        # Beide Quellen nebenlaeufig: der Knoten ueber RPC, das Histogramm
        # ueber Electrum. Zusammen gemessen unter 60 ms.
        # Die Startseite zeigt nur noch Suche, Blockkette und Gebuehren. Sie
        # braucht deshalb weder den vollen Knotenzustand noch die Tiefenkarte -
        # das spart bei jedem Aufruf mehrere RPC-Runden.
        s = await spiel.fuer_seite(TOR, t)
        return vorlagen.TemplateResponse(request, "start.html", {
            "t": t,
            "pfad": request.url.path,
            "s": s,
        })

    def _btc_lokal(sat, t):
        s = _btc(sat)
        return s.replace(".", ",") if t.sprache == "de" else s

    async def adressseite(request):
        t = Texte(sprache_aus_cookies(request.cookies))
        wert = request.path_params["adresse"]
        try:
            kennung, art = adresse.scripthash(wert)
        except adresse.UnbekannteAdresse:
            return vorlagen.TemplateResponse(request, "start.html", {
                "t": t, "z": await knoten.zustand(TOR), "pfad": "/",
                "dauer": lambda s: _dauer_text(s, t.sprache),
                "suchwert": wert, "fehler": t.t("search.unknown"),
            }, status_code=404)

        u = await elektrum.adress_uebersicht(kennung)
        if u is None:
            # Kein erfundener Saldo, wenn der Index nicht antwortet.
            u = {"bestaetigt_sat": None, "offen_sat": 0, "anzahl": 0,
                 "anzahl_offen": 0, "zu_gross": False, "verlauf": []}
        # Offene Ausgaenge, Erstbenutzung, QR-Code und Staubgrenze. Bekommt die
        # bereits geholten Bewegungen mit, damit Fulcrum nicht zweimal dieselbe
        # Historie liefern muss.
        # bewegungen ist die ANZAHL, nicht die Liste - damit das Modul weiss,
        # ob es die offenen Ausgaenge ueberhaupt auflisten soll.
        ad = await adressdetails.details(TOR, kennung, wert, art,
                                         u.get("anzahl") if u else None)
        return vorlagen.TemplateResponse(request, "adresse.html", {
            "t": t, "pfad": request.url.path, "adresse": wert, "art": art,
            "kurz": wert[:12] + "\u2026", "u": u, "ad": ad,
            "btc": lambda s: _btc_lokal(s, t),
        })

    async def suche(request):
        """Eine Eingabezeile fuer alles - der Nutzer soll nicht wissen muessen,
        was er da eigentlich eingibt.

        Erkennt Blockhoehe, Blockhash, Txid, Adresse und xpub. Vorher konnte sie
        NUR Adressen; wer eine Txid eingab, bekam "nicht erkannt" - die
        auffaelligste Luecke im ganzen Projekt.
        """
        wert = (request.query_params.get("q") or "").strip()
        art, ziel = await suchen.einordnen(TOR, wert)
        if ziel:
            return RedirectResponse(ziel, status_code=303)
        t = Texte(sprache_aus_cookies(request.cookies))
        return vorlagen.TemplateResponse(request, "start.html", {
            "t": t, "z": await knoten.zustand(TOR), "pfad": "/",
            "dauer": lambda s: _dauer_text(s, t.sprache),
            "suchwert": wert, "fehler": t.t("search.unknown") if wert else None,
        }, status_code=404 if wert else 200)

    async def knotenseite_(request):
        t = Texte(sprache_aus_cookies(request.cookies))
        # Drei Quellen nebenlaeufig - der Knoten ueber RPC, das Histogramm ueber
        # Electrum, die Kennzahlen wieder ueber RPC. Nacheinander waere es die
        # Summe der Wartezeiten statt der laengsten.
        k, hist, s = await asyncio.gather(
            knotenseite.seite(TOR), tiefenkarte.histogramm(),
            spiel.fuer_seite(TOR, t))
        return vorlagen.TemplateResponse(request, "knoten.html", {
            "t": t, "pfad": request.url.path,
            "k": k, "karte": tiefenkarte.karte(hist), "s": s,
        })

    async def blockseite_(request):
        t = Texte(sprache_aus_cookies(request.cookies))
        b = await blockseite.blockdaten(TOR, request.path_params["kennung"],
                                        t.sprache)
        # Ein unbekannter Block ist kein Serverfehler, aber auch kein Treffer:
        # 404 ist die ehrliche Antwort und haelt Suchmaschinen davon ab,
        # erfundene Hoehen zu indizieren.
        return vorlagen.TemplateResponse(
            request, "block.html", {"t": t, "pfad": request.url.path, "b": b},
            status_code=200 if b.get("gefunden") else 404)

    async def txseite_(request):
        t = Texte(sprache_aus_cookies(request.cookies))
        d = await txseite.transaktion(TOR, request.path_params["txid"])
        return vorlagen.TemplateResponse(request, "tx.html", {
            "t": t, "pfad": request.url.path, "d": d,
            "befunde": txseite.befunde(d, t) if d and d.get("gefunden") else [],
            "btc": lambda s: _btc_lokal(s, t),
            "dauer": lambda s: _dauer_text(s, t.sprache),
            "spanne": lambda s: txseite.spanne(s, t),
        }, status_code=200 if (d and d.get("gefunden")) else 404)

    async def blockliste_(request):
        t = Texte(sprache_aus_cookies(request.cookies))
        vor = blockliste.vor_aus_parameter(request.query_params.get("vor"))
        return vorlagen.TemplateResponse(request, "blocks.html", {
            "t": t, "pfad": request.url.path,
            "bl": await blockliste.liste(TOR, vor),
            "dauer": lambda s: _dauer_text(s, t.sprache),
        })

    async def mempoolseite_(request):
        t = Texte(sprache_aus_cookies(request.cookies))
        m = await mempoolseite.seite(TOR)
        return vorlagen.TemplateResponse(request, "mempool.html", {
            "t": t, "pfad": request.url.path, "m": m,
            "s": await spiel.fuer_seite(TOR, t),
        })

    async def gebuehrenseite_(request):
        t = Texte(sprache_aus_cookies(request.cookies))
        return vorlagen.TemplateResponse(request, "gebuehren.html", {
            "t": t, "pfad": request.url.path, "g": await gebuehren.seite(TOR),
        })

    async def miningseite_(request):
        t = Texte(sprache_aus_cookies(request.cookies))
        m = await mining.uebersicht(TOR, t.sprache)
        return vorlagen.TemplateResponse(request, "mining.html", {
            "t": t, "pfad": request.url.path, "m": m, "h": m,
        })

    async def xpubseite_(request):
        t = Texte(sprache_aus_cookies(request.cookies))
        # Ein erweiterter oeffentlicher Schluessel beschreibt eine ganze Wallet.
        # Er wird NICHT protokolliert und verlaesst den Knoten nie - das ist der
        # ganze Grund, warum es diese Ansicht nur beim Selberhosten geben darf.
        # xpub.uebersicht braucht kein RPC-Tor: sie leitet die Adressen
        # selbst ab und holt die Salden ueber den Electrum-Server.
        x = await xpub.uebersicht(request.path_params["schluessel"], t.sprache)
        return vorlagen.TemplateResponse(
            request, "xpub.html",
            {"t": t, "pfad": request.url.path, "x": x},
            status_code=200 if (x and x.get("gefunden")) else 404)

    async def sprache_setzen(request):
        wahl = request.path_params["sprache"]
        ziel = request.query_params.get("weiter", "/")
        # Nur eigene Pfade - sonst waere das eine offene Weiterleitung.
        if not ziel.startswith("/") or ziel.startswith("//"):
            ziel = "/"
        antwort = RedirectResponse(ziel, status_code=303)
        if wahl in SPRACHEN:
            antwort.set_cookie(COOKIE, wahl, max_age=COOKIE_ALTER,
                               samesite="lax", httponly=False, path="/")
        return antwort

    return Starlette(routes=[
        Route("/", startseite),
        Route("/suche", suche),
        Route("/address/{adresse}", adressseite),
        Route("/node", knotenseite_),
        Route("/block/{kennung}", blockseite_),
        Route("/tx/{txid}", txseite_),
        Route("/api/kette", kette.handler(TOR)),
        Route("/blocks", blockliste_),
        Route("/mempool", mempoolseite_),
        Route("/gebuehren", gebuehrenseite_),
        Route("/mining", miningseite_),
        Route("/xpub/{schluessel}", xpubseite_),
        Route("/sprache/{sprache}", sprache_setzen),
        Mount("/statisch", StaticFiles(directory=os.path.join(HIER, "statisch")),
              name="statisch"),
    ])


def main():
    import uvicorn
    uvicorn.run(erzeuge_app(), host="0.0.0.0",
                port=int(os.environ.get("SATSCOPE_PORT", "8000")))


if __name__ == "__main__":
    main()
