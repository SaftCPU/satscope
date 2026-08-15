"""Der Sammler. Einziger Schreiber der Datenbank.

Getrennt vom Web-Dienst, damit ein Absturz hier die Oberflaeche nicht mitreisst
und damit SQLite-WAL genau einen Schreiber sieht.

Stand 0.1.0: Geruest.

WICHTIG fuer den weiteren Bau - kein Vollscan bei der Installation. Ein
Rueckwaertslauf ueber die ganze Kette kostet 252 ms je Block, bei 962.608
Bloecken sind das 67,5 Stunden Dauerlast auf einem fremden Knoten. Der Index
beginnt bei "jetzt" und waechst vorwaerts; Historie spaeter und gedrosselt.
"""
import os


def main():
    print("satscope-sammler %s - Geruest, noch ohne Arbeit" %
          os.environ.get("SATSCOPE_ROLLE", "?"))


if __name__ == "__main__":
    main()
