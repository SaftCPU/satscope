"""Selbsttest ohne Netz und ohne Abhaengigkeiten.

    python3 -m satscope.selbsttest

Prueft genau die Stellen, an denen Zweisprachigkeit erfahrungsgemaess bricht.
"""
import sys

from . import sprache
from .rpc import BILLIG, NichtErlaubt, Tor
from .rpc_teuer import TEUER

FEHLER = []


def pruefe(name, ist, soll):
    if ist != soll:
        FEHLER.append("%s: %r erwartet, %r bekommen" % (name, soll, ist))
        print("  FEHLER  %s" % name)
    else:
        print("  ok      %s" % name)


def main():
    print("Zahlformatierung")
    pruefe("englisch, zwei Stellen", sprache.zahl(1234.56, 2, "en"), "1,234.56")
    pruefe("deutsch, zwei Stellen", sprache.zahl(1234.56, 2, "de"), "1.234,56")
    pruefe("englisch, ganze Zahl", sprache.zahl(962607, 0, "en"), "962,607")
    pruefe("deutsch, ganze Zahl", sprache.zahl(962607, 0, "de"), "962.607")
    pruefe("Millionen deutsch", sprache.zahl(1234567.8, 1, "de"), "1.234.567,8")
    pruefe("None wird zum Strich", sprache.zahl(None), "\u2013")

    print("\nTextkatalog")
    en, de = sprache.Texte("en"), sprache.Texte("de")
    pruefe("Vorgabe ist Englisch",
           sprache.sprache_aus_cookies({}), "en")
    pruefe("gueltige Wahl greift",
           sprache.sprache_aus_cookies({sprache.COOKIE: "de"}), "de")
    pruefe("unsinnige Wahl faellt zurueck",
           sprache.sprache_aus_cookies({sprache.COOKIE: "kl"}), "en")

    print("\nBenannte Platzhalter (unterschiedliche Wortstellung)")
    pruefe("englischer Satz", en.t("block.cdd", cdd="2,542"),
           "This block destroyed 2,542 coin days.")
    pruefe("deutscher Satz", de.t("block.cdd", cdd="2.542"),
           "Dieser Block hat 2.542 Coin-Tage vernichtet.")

    print("\nroh(): Vorlage MIT Platzhaltern fuer das JavaScript")
    pruefe("Vorlage bleibt unveraendert", de.roh("block.cdd"),
           "Dieser Block hat {cdd} Coin-Tage vernichtet.")
    pruefe("unbekannter Schluessel faellt auf", en.roh("gibt.es.nicht"),
           "!gibt.es.nicht!")

    print("\nLuecken fallen auf, statt still zu sein")
    pruefe("unbekannter Schluessel", en.t("gibt.es.nicht"), "!gibt.es.nicht!")
    pruefe("fehlender Platzhalter",
           en.t("block.cdd"), "!block.cdd ohne 'cdd'!")

    print("\nVollstaendigkeit der Kataloge")
    fehlend = sorted(set(sprache._laden("en")) - set(sprache._laden("de")))
    pruefe("kein Schluessel fehlt auf Deutsch", fehlend, [])
    ueber = sorted(set(sprache._laden("de")) - set(sprache._laden("en")))
    pruefe("kein Schluessel ist nur auf Deutsch da", ueber, [])

    print("\nAdressen: gueltige Pruefsumme ist nicht genug")
    from .adresse import UnbekannteAdresse, script_von_adresse
    for schlecht, warum in (("bc1gmk9yu", "leere Nutzlast, Pruefsumme gueltig"),
                            ("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5", "Pruefsumme falsch"),
                            ("tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx", "Testnet")):
        try:
            script_von_adresse(schlecht)
            FEHLER.append("%s wurde akzeptiert" % schlecht)
            print("  FEHLER  %s" % warum)
        except UnbekannteAdresse:
            print("  ok      %s" % warum)

    print("\nRPC-Tor: Kostenklassen sind wirklich getrennt")
    tor = Tor()
    pruefe("billige Methode ist bekannt", tor.kennt("getblockchaininfo"), True)
    pruefe("teure Methode ist im Web-Tor UNbekannt", tor.kennt("getblock"), False)
    pruefe("Klassen ueberschneiden sich nicht", sorted(BILLIG & TEUER), [])
    try:
        import asyncio
        asyncio.run(tor.ruf("gettxoutsetinfo"))
        FEHLER.append("teurer Aufruf wurde NICHT abgewiesen")
        print("  FEHLER  teurer Aufruf abgewiesen")
    except NichtErlaubt:
        print("  ok      teurer Aufruf abgewiesen")

    print("\n%d Pruefungen, %d Fehler" % (23, len(FEHLER)))
    for f in FEHLER:
        print("  " + f)
    return 1 if FEHLER else 0


if __name__ == "__main__":
    sys.exit(main())
