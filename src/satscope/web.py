"""Der Web-Dienst. Liest nur, schreibt nie.

Bewusst KEIN FastAPI (Pydantic-Ballast ohne Nutzen, wir bauen kein JSON-Produkt)
und kein Flask (kann WebSocket-Push und Hintergrundaufgaben im selben Prozess
nur mit Zusatzmaschinerie).

Der Web-Prozess bekommt AUSSCHLIESSLICH das billige RPC-Tor. rpc_teuer wird hier
absichtlich nicht importiert: gettxoutsetinfo braucht am Knoten gemessene 58 s -
ein unbedachter Handler waere ein Denial-of-Service gegen den eigenen Node.
"""
import os

from . import adresse, elektrum, knoten
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
        z = await knoten.zustand(TOR)
        return vorlagen.TemplateResponse(request, "start.html", {
            "t": t,
            "z": z,
            "pfad": request.url.path,
            "dauer": lambda s: _dauer_text(s, t.sprache),
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
        return vorlagen.TemplateResponse(request, "adresse.html", {
            "t": t, "pfad": request.url.path, "adresse": wert, "art": art,
            "kurz": wert[:12] + "\u2026", "u": u,
            "btc": lambda s: _btc_lokal(s, t),
        })

    async def suche(request):
        """Eine Eingabezeile fuer alles - der Nutzer soll nicht wissen muessen,
        was er da eigentlich eingibt."""
        wert = (request.query_params.get("q") or "").strip()
        try:
            adresse.scripthash(wert)
            return RedirectResponse("/address/" + wert, status_code=303)
        except adresse.UnbekannteAdresse:
            pass
        t = Texte(sprache_aus_cookies(request.cookies))
        return vorlagen.TemplateResponse(request, "start.html", {
            "t": t, "z": await knoten.zustand(TOR), "pfad": "/",
            "dauer": lambda s: _dauer_text(s, t.sprache),
            "suchwert": wert, "fehler": t.t("search.unknown") if wert else None,
        }, status_code=404 if wert else 200)

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
