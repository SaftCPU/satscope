"""Der Web-Dienst. Liest nur, schreibt nie.

Bewusst KEIN FastAPI (Pydantic-Ballast ohne Nutzen, wir bauen kein JSON-Produkt)
und kein Flask (kann WebSocket-Push und Hintergrundaufgaben im selben Prozess
nur mit Zusatzmaschinerie).

Der Web-Prozess bekommt AUSSCHLIESSLICH das billige RPC-Tor. rpc_teuer wird hier
absichtlich nicht importiert: gettxoutsetinfo braucht am Knoten gemessene 58 s -
ein unbedachter Handler waere ein Denial-of-Service gegen den eigenen Node.
"""
import os

from . import knoten
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
