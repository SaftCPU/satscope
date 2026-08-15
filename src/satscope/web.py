"""Der Web-Dienst. Liest nur, schreibt nie.

Bewusst KEIN FastAPI (Pydantic-Ballast ohne Nutzen, wir bauen kein JSON-Produkt)
und kein Flask (kann WebSocket-Push und Hintergrundaufgaben im selben Prozess
nur mit Zusatzmaschinerie).

Stand 0.1.0: Geruest. Die Routen aus dem Bauplan sind angelegt, aber noch nicht
gefuellt - was hier steht, ist ehrlich unfertig und behauptet nichts anderes.
"""
import os

from .rpc import BILLIG, Tor
from .sprache import COOKIE, COOKIE_ALTER, SPRACHEN, Texte, sprache_aus_cookies

# Der Web-Prozess bekommt AUSSCHLIESSLICH das billige Tor. rpc_teuer wird hier
# absichtlich nicht importiert - siehe rpc.py.
TOR = Tor(erlaubt=BILLIG)


def erzeuge_app():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse, RedirectResponse
    from starlette.routing import Route

    async def startseite(request):
        t = Texte(sprache_aus_cookies(request.cookies))
        return PlainTextResponse(
            "%s - %s\n\n%s\n" % (t.t("app.name"), t.t("app.tagline"),
                                   t.t("footer.nocalls")))

    async def sprache_setzen(request):
        wahl = request.path_params["sprache"]
        ziel = request.query_params.get("weiter", "/")
        if not ziel.startswith("/"):     # keine offene Weiterleitung
            ziel = "/"
        antwort = RedirectResponse(ziel, status_code=303)
        if wahl in SPRACHEN:
            antwort.set_cookie(COOKIE, wahl, max_age=COOKIE_ALTER,
                               samesite="lax", httponly=False, path="/")
        return antwort

    return Starlette(routes=[
        Route("/", startseite),
        Route("/sprache/{sprache}", sprache_setzen),
    ])


def main():
    import uvicorn
    uvicorn.run(erzeuge_app(), host="0.0.0.0",
                port=int(os.environ.get("SATSCOPE_PORT", "8000")))


if __name__ == "__main__":
    main()
