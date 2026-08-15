"""Die Knotenseite /node - der eigene Knoten als vollwertiges Objekt.

Warum diese Seite ueberhaupt: ein fremder Explorer zeigt die Siegerkette. DEIN
Knoten kennt auch die verworfenen Zweige, seine eigenen Gegenstellen, den
Verkehr, den er dem Netz geschenkt hat, und den Stand der Soft-Forks. Das ist
genau das, was ein Explorer im Internet einem nie sagen kann - deshalb steht es
hier in ganzen Saetzen und nicht als Feldliste.

⚠️ DATENSCHUTZ - die harte Auflage dieser Seite:
Es verlaesst NIEMALS eine IP-Adresse dieses Modul. Weder die der Gegenstellen
(`addr`, `addrbind`) noch die eigene (`addrlocal` je Gegenstelle,
`getnetworkinfo.localaddresses`). Die eigene Onion-/I2P-Adresse wuerde den
Knoten des Nutzers im Netz auffindbar machen; die Adresse einer Gegenstelle
geht niemanden etwas an.
Durchgesetzt wird das mit einer WHITELIST (GEGENSTELLE_FELDER): was dort nicht
steht, kommt gar nicht erst in die Oberflaeche. Eine Blacklist waere hier der
falsche Weg - bitcoind bekommt mit jeder Fassung neue Felder, und ein neues
Adressfeld waere still dabei, ohne dass jemand die Liste anfasst.

KOSTEN (am Knoten gemessen, 15.08.2026): getnetworkinfo 15 ms, getpeerinfo
8-15 ms, getnettotals 7 ms, getblockchaininfo 9 ms, getmininginfo 7 ms,
getdeploymentinfo 7-10 ms, getchaintips 114 ms. Alles aus rpc.BILLIG. Nur
getchaintips ist ein Grenzfall, deshalb liegt genau davor ein kleiner
Zeitcache - siehe _spitzen_holen().

Selbstpruefung ohne Knoten und ohne Netz:  python3 -m satscope.knotenseite
"""
import asyncio
import time

from .rpc import RpcFehler

# ---------------------------------------------------------------- Datenschutz
# Nur diese Felder einer Gegenstelle existieren fuer die Oberflaeche. Jedes
# einzelne ist begruendet; wer eins ergaenzt, muss die Begruendung mitliefern.
#   network                Netzart (ipv4/ipv6/onion/i2p/cjdns) - der Balken
#   inbound                Richtung
#   connection_type        block-relay-only und manuell sind eigene Aussagen
#   pingtime               Antwortzeit
#   bytessent/bytesrecv    Verkehr je Netzart
#   version                Protokollfassung der Gegenstelle
#   subver                 Software der Gegenstelle (⚠️ frei waehlbarer Text!)
#   conntime               "laengste Verbindung besteht seit ..."
#   transport_protocol_type  v1/v2 - ob die Verbindung verschluesselt ist
# NICHT dabei und niemals: addr, addrbind, addrlocal, id, servicesnames.
GEGENSTELLE_FELDER = frozenset({
    "network", "inbound", "connection_type", "pingtime",
    "bytessent", "bytesrecv", "version", "subver", "conntime",
    "transport_protocol_type",
})

# Aus getnetworkinfo.networks ebenso nur das Noetige. `proxy` bleibt draussen:
# es nennt den lokalen Tor-Port und damit einen Teil der Einrichtung.
NETZ_FELDER = frozenset({"name", "reachable", "limited"})

# Netze in FESTER Reihenfolge - der Balken darf beim Neuladen nicht die Farben
# tauschen, sonst kann man ihn nicht wiedererkennen.
NETZE = ("ipv4", "ipv6", "onion", "i2p", "cjdns", "not_publicly_routable")
ANONYM = frozenset({"onion", "i2p", "cjdns"})

# Zustaende, fuer die es einen erklaerenden Text gibt. Kommt in einer kuenftigen
# Core-Fassung ein neuer dazu, zeigt die Vorlage den Rohwert - besser als ein
# sichtbares "!tips.state.xyz!".
SPITZEN_ZUSTAENDE = frozenset({
    "active", "valid-fork", "valid-headers", "headers-only", "invalid"})
FORK_ZUSTAENDE = frozenset({
    "defined", "started", "locked_in", "active", "failed"})
# Soft-Forks, zu denen ein Erklaersatz im Katalog steht (forks.about.*).
FORK_ERKLAERT = frozenset({"bip34", "bip65", "bip66", "csv", "segwit", "taproot"})
FORK_NAMEN = {"bip34": "BIP 34", "bip65": "BIP 65", "bip66": "BIP 66",
              "csv": "CSV", "segwit": "SegWit", "taproot": "Taproot",
              "testdummy": "Testdummy"}

_SI = ("", "k", "M", "G", "T", "P", "E", "Z")


# ------------------------------------------------------------------ Werkzeug
async def _sicher(tor, methode, *argumente):
    """Ruft auf und liefert None statt zu werfen.

    Bewusst hier noch einmal geschrieben statt aus knoten.py importiert: das ist
    ein privater Name eines fremden Moduls, und faellt EIN Aufruf aus, soll
    genau diese Zahl fehlen - nicht die Seite. Sechs Zeilen Doppelung sind der
    Preis dafuer, dass beide Seiten unabhaengig bleiben.
    """
    try:
        return await tor.ruf(methode, *argumente)
    except (RpcFehler, OSError, asyncio.TimeoutError):
        return None


def _skaliert(wert, grundeinheit="", basis=1000.0):
    """Grosse Zahl mit SI-Vorsatz: 6,68 TB statt 6.680.000.000.000 Byte.

    Liefert {wert, einheit, stellen} - formatiert wird erst in der Vorlage mit
    t.zahl(), weil nur die die Sprache kennt (1.234,56 gegen 1,234.56).
    Die Einheitenzeichen selbst kommen NICHT aus dem Katalog: SI-Vorsaetze sind
    in beiden Sprachen dieselben - genau wie das "MB" in start.html.
    Dezimal (1 kB = 1000 B), damit dieselbe Zahl auf zwei Seiten nicht
    zweierlei bedeutet.
    """
    if wert is None:
        return None
    try:
        w = float(wert)
    except (TypeError, ValueError):
        return None
    i = 0
    while abs(w) >= basis and i < len(_SI) - 1:
        w /= basis
        i += 1
    # Wenige signifikante Stellen: "6,68 TB" liest sich, "6,6800 TB" nicht.
    stellen = 2 if abs(w) < 10 else (1 if abs(w) < 100 else 0)
    return {"wert": w, "einheit": _SI[i] + grundeinheit, "stellen": stellen}


def _text_saeubern(wert, laenge=40):
    """Fremdtext auf druckbares ASCII kuerzen.

    ⚠️ `subver` waehlt die GEGENSTELLE frei. Jinja2 maskiert zwar (autoescape),
    aber ein Feld, das ein Fremder fuellt, wird hier trotzdem beschnitten,
    bevor es irgendwo landet - Steuerzeichen und Ueberlaenge haben in der
    Oberflaeche nichts verloren.
    """
    if not isinstance(wert, str):
        return None
    sauber = "".join(z for z in wert if 32 <= ord(z) < 127).strip()
    return sauber[:laenge] or None


def _zerlege_subver(subversion):
    """('Satoshi'|'Knots'|..., '29.0.0') aus '/Satoshi:29.0.0/'."""
    sauber = _text_saeubern(subversion, 60)
    if not sauber:
        return None, ""
    teile = [t for t in sauber.strip("/").split("/") if t]
    if not teile:
        return None, ""
    letzt = teile[-1]
    name, _, fassung = letzt.partition(":")
    # Knots meldet sich als "Satoshi:27.1.0(knots20240801)" - es IST nicht Core.
    if "knots" in letzt.lower():
        name = "Knots"
    # Nur der fuehrende Zahlenteil: "27.1.0(knots20240801)" -> "27.1.0".
    ziffern = ""
    for z in fassung:
        if z.isdigit() or z == ".":
            ziffern += z
        else:
            break
    return (name or None), ziffern.strip(".")


def software_name(subversion):
    """'/Satoshi:29.0.0/' -> 'Core 29.0'.

    Nur Haupt- und Nebenfassung, damit sich die GEGENSTELLEN ueberhaupt zu
    Gruppen zusammenfassen lassen - mit Patchstand waeren es dreissig Zeilen
    mit je einer Gegenstelle darin.
    """
    name, fassung = _zerlege_subver(subversion)
    if not name:
        return None
    if name == "Satoshi":
        name = "Core"
    return (name + " " + ".".join(fassung.split(".")[:2])).strip() or None


def eigene_software(subversion):
    """'/Satoshi:29.0.0/' -> 'Bitcoin Core 29.0.0'.

    Beim EIGENEN Knoten mit vollem Patchstand - man will genau wissen, was da
    laeuft, und gruppiert wird hier nichts. Laesst sich die Angabe nicht
    zerlegen, steht die gesaeuberte Rohform da: besser als ein Strich, denn die
    Zeile ist ja beantwortet, nur nicht in unserer Form.
    """
    name, fassung = _zerlege_subver(subversion)
    if not name:
        return _text_saeubern(subversion, 40)
    if name == "Satoshi":
        name = "Bitcoin Core"
    elif name == "Knots":
        name = "Bitcoin Knots"
    return (name + " " + fassung).strip()


def _liste(wert):
    """warnings ist in Core 29 eine Liste, davor ein einzelner String."""
    if not wert:
        return []
    if isinstance(wert, str):
        return [wert]
    if isinstance(wert, (list, tuple)):
        return [w for w in wert if isinstance(w, str) and w.strip()]
    return []


def _mitte(werte):
    """Median. Der Mittelwert waere hier falsch: eine einzige Gegenstelle
    hinter einem haengenden Tor-Kreis zieht ihn um Hunderte Millisekunden."""
    if not werte:
        return None
    s = sorted(werte)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def _anteil(teil, ganz):
    return (100.0 * teil / ganz) if ganz else None


# ------------------------------------------------------------- Gegenstellen
def saeubern(rohe):
    """Whitelist-Projektion auf die Felder aus GEGENSTELLE_FELDER.

    Das ist die Stelle, an der der Datenschutz durchgesetzt wird. Alles, was
    danach kommt, sieht die Adressen gar nicht mehr - man kann sie also auch
    nicht versehentlich in eine Vorlage schreiben.
    """
    raus = []
    for g in rohe or []:
        if isinstance(g, dict):
            raus.append({k: v for k, v in g.items() if k in GEGENSTELLE_FELDER})
    return raus


def gegenstellen(rohe, netze_roh=None, jetzt=None):
    """Alles ueber die Gegenstellen - ohne zu verraten, wer sie sind."""
    g = saeubern(rohe)
    jetzt = int(jetzt if jetzt is not None else time.time())
    anzahl = len(g)

    zaehler, gesendet, empfangen = {}, {}, {}
    for e in g:
        netz = e.get("network") if e.get("network") in NETZE else "not_publicly_routable"
        zaehler[netz] = zaehler.get(netz, 0) + 1
        gesendet[netz] = gesendet.get(netz, 0) + int(e.get("bytessent") or 0)
        empfangen[netz] = empfangen.get(netz, 0) + int(e.get("bytesrecv") or 0)

    # Welche Netze der Knoten ueberhaupt erreichen kann. Interessant wird das
    # erst im Abgleich: "Tor ist erreichbar, aber keine einzige Gegenstelle
    # nutzt es" ist ein Befund, den man sonst nirgends sieht.
    erreichbar = set()
    for n in netze_roh or []:
        if isinstance(n, dict) and n.get("reachable") and not n.get("limited"):
            name = n.get("name")
            if name in NETZE:
                erreichbar.add(name)

    netze = []
    for name in NETZE:
        n = zaehler.get(name, 0)
        if not n and name not in erreichbar:
            continue
        netze.append({
            "schluessel": name,
            "anzahl": n,
            "anteil": _anteil(n, anzahl),
            "gesendet": _skaliert(gesendet.get(name, 0), "B"),
            "empfangen": _skaliert(empfangen.get(name, 0), "B"),
            "erreichbar": name in erreichbar,
        })

    pings = [float(e["pingtime"]) * 1000.0 for e in g
             if isinstance(e.get("pingtime"), (int, float)) and e["pingtime"] > 0]
    zeiten = [int(e["conntime"]) for e in g
              if isinstance(e.get("conntime"), int) and 0 < e["conntime"] <= jetzt]

    gruppen = {}
    for e in g:
        name = software_name(e.get("subver")) or "?"
        gruppen[name] = gruppen.get(name, 0) + 1
    software = sorted(gruppen.items(), key=lambda p: (-p[1], p[0]))[:6]

    anonym = sum(z for netz, z in zaehler.items() if netz in ANONYM)
    v2 = sum(1 for e in g if e.get("transport_protocol_type") == "v2")

    liste = [{
        "netz": e.get("network") if e.get("network") in NETZE else "not_publicly_routable",
        "eingehend": bool(e.get("inbound")),
        "art": _text_saeubern(e.get("connection_type"), 24),
        "ping_ms": (float(e["pingtime"]) * 1000.0
                    if isinstance(e.get("pingtime"), (int, float)) and e["pingtime"] > 0
                    else None),
        "software": software_name(e.get("subver")),
        "protokoll": e.get("version") if isinstance(e.get("version"), int) else None,
        "v2": e.get("transport_protocol_type") == "v2",
    } for e in g]
    # Nach Netz (feste Reihenfolge), dann eingehend/ausgehend, dann Ping.
    liste.sort(key=lambda e: (NETZE.index(e["netz"]), not e["eingehend"],
                              e["ping_ms"] if e["ping_ms"] is not None else 9e9))

    return {
        "anzahl": anzahl,
        "eingehend": sum(1 for e in g if e.get("inbound")),
        "ausgehend": sum(1 for e in g if not e.get("inbound")),
        "netze": netze,
        "anonym": anonym,
        "anonym_anteil": _anteil(anonym, anzahl),
        # Nur Blocklinien: sie sehen die eigenen Transaktionen nie und sind
        # damit der wirksamste Schutz gegen Zuordnung ueber die Erstankuendigung.
        "nur_bloecke": sum(1 for e in g
                           if e.get("connection_type") == "block-relay-only"),
        "manuell": sum(1 for e in g if e.get("connection_type") == "manual"),
        "verschluesselt": v2,
        "verschluesselt_anteil": _anteil(v2, anzahl),
        "ping_best": min(pings) if pings else None,
        "ping_mitte": _mitte(pings),
        "ping_schlecht": max(pings) if pings else None,
        "aelteste_stunden": ((jetzt - min(zeiten)) / 3600.0) if zeiten else None,
        "software": [{"name": n, "anzahl": z} for n, z in software],
        "liste": liste,
        "stille": [n["schluessel"] for n in netze
                   if n["erreichbar"] and not n["anzahl"]],
    }


# ------------------------------------------------------------- Kettenspitzen
def spitzen(rohe):
    """Konkurrierende Kettenspitzen zusammenfassen.

    Ein lange laufender Knoten sammelt Dutzende reiner Kopfzeilen-Spitzen an;
    ungefiltert waere das eine Bleiwueste. Gezaehlt wird alles, aufgelistet
    werden die hoechsten sechs Zweige.
    """
    if rohe is None:
        return None
    tips = [t for t in rohe if isinstance(t, dict)]
    aktiv = next((t for t in tips if t.get("status") == "active"), None)
    zweige = [t for t in tips if t is not aktiv]
    zweige.sort(key=lambda t: (t.get("height") or 0), reverse=True)

    def eintrag(t):
        h = t.get("hash") if isinstance(t.get("hash"), str) else None
        stand = t.get("status")
        return {
            "hoehe": t.get("height"),
            "laenge": t.get("branchlen"),
            "stand": stand,
            # None heisst: kein Erklaertext im Katalog, Vorlage zeigt den Rohwert.
            "stand_schluessel": stand if stand in SPITZEN_ZUSTAENDE else None,
            # Die LETZTEN Zeichen: die ersten sechzehn eines Blockhashs sind
            # heute samt und sonders Nullen und unterscheiden gar nichts.
            "kurz": ("…" + h[-16:]) if h and len(h) > 16 else h,
        }

    return {
        "anzahl": len(tips),
        "zweige": len(zweige),
        "aktiv_hoehe": (aktiv or {}).get("height"),
        "liste": [eintrag(t) for t in zweige[:6]],
        "weitere": max(0, len(zweige) - 6),
    }


# ----------------------------------------------------------------- Soft-Forks
def softforks(roh, hoehe=None):
    """getdeploymentinfo in drei Gruppen: laufend, aktiv, ruhend."""
    if not isinstance(roh, dict):
        return None
    laufend, aktiv, ruhend = [], [], []
    for name, w in sorted((roh.get("deployments") or {}).items()):
        if not isinstance(w, dict):
            continue
        b9 = w.get("bip9") if isinstance(w.get("bip9"), dict) else {}
        st = b9.get("statistics") if isinstance(b9.get("statistics"), dict) else {}
        stand = b9.get("status")
        e = {
            "name": name,
            "anzeige": FORK_NAMEN.get(name, name),
            # Erklaersatz nur, wo es ihn im Katalog wirklich gibt.
            "erklaerung": ("forks.about." + name) if name in FORK_ERKLAERT else None,
            "aktiv": bool(w.get("active")),
            "hoehe": w.get("height"),
            "abstand": ((hoehe - w["height"])
                        if isinstance(hoehe, int) and isinstance(w.get("height"), int)
                        else None),
            "stand": stand if stand in FORK_ZUSTAENDE else None,
            "bit": b9.get("bit"),
            "signale": st.get("count"),
            "schwelle": st.get("threshold"),
            "abgelaufen": st.get("elapsed"),
            "periode": st.get("period"),
            "moeglich": st.get("possible", True),
            "ab_hoehe": b9.get("min_activation_height"),
        }
        if e["signale"] is not None and e["schwelle"]:
            e["breite"] = min(100.0, round(100.0 * e["signale"] / e["schwelle"], 1))
        else:
            e["breite"] = None
        if e["aktiv"]:
            aktiv.append(e)
        elif stand in ("started", "locked_in"):
            laufend.append(e)
        else:
            ruhend.append(e)
    aktiv.sort(key=lambda e: e["hoehe"] if isinstance(e["hoehe"], int) else 0)
    return {"laufend": laufend, "aktiv": aktiv, "ruhend": ruhend,
            "alle_aktiv": not laufend and bool(aktiv)}


# ------------------------------------------------------------------- Verkehr
def verkehr(roh):
    """getnettotals - was der Knoten dem Netz gegeben und genommen hat."""
    if not isinstance(roh, dict):
        return None
    gesendet = roh.get("totalbytessent")
    empfangen = roh.get("totalbytesrecv")
    ziel = roh.get("uploadtarget") if isinstance(roh.get("uploadtarget"), dict) else {}
    grenze = ziel.get("target") or 0
    rest = ziel.get("bytes_left_in_cycle") or 0
    benutzt = max(0, grenze - rest) if grenze else None
    return {
        "gesendet": _skaliert(gesendet, "B"),
        "empfangen": _skaliert(empfangen, "B"),
        "verhaeltnis": ((gesendet / empfangen)
                        if gesendet is not None and empfangen else None),
        "gibt_mehr": bool(gesendet is not None and empfangen
                          and gesendet > empfangen),
        "grenze": _skaliert(grenze, "B") if grenze else None,
        "rest": _skaliert(rest, "B") if grenze else None,
        "grenze_breite": (min(100.0, round(100.0 * benutzt / grenze, 1))
                          if grenze else None),
        "grenze_erreicht": bool(ziel.get("target_reached")),
        "alte_bloecke": bool(ziel.get("serve_historical_blocks", True)),
    }


# ---------------------------------------------------------------- die Seite
# getchaintips kostet 114 ms - das Zehnfache der uebrigen Aufrufe zusammen.
# Bei einem Neuladen im Sekundentakt (F5 ist die haeufigste Nutzergeste) waere
# das eine unnoetige Dauerlast auf einem Knoten, auf dem echtes Geld liegt.
# Kettenspitzen aendern sich hoechstens im Blocktakt, 30 s Cache verlieren also
# nichts. Die Sperre verhindert, dass gleichzeitige Aufrufe alle durchrutschen.
_SPITZEN_TTL = 30.0
_spitzen_cache = {"zeit": 0.0, "wert": None}
_spitzen_sperre = asyncio.Lock()


async def _spitzen_holen(tor, jetzt):
    if _spitzen_cache["wert"] is not None and \
            jetzt - _spitzen_cache["zeit"] < _SPITZEN_TTL:
        return _spitzen_cache["wert"]
    async with _spitzen_sperre:
        # Nach dem Warten noch einmal schauen: ein anderer Aufruf war
        # vielleicht schneller.
        if _spitzen_cache["wert"] is not None and \
                jetzt - _spitzen_cache["zeit"] < _SPITZEN_TTL:
            return _spitzen_cache["wert"]
        wert = await _sicher(tor, "getchaintips")
        if wert is not None:
            _spitzen_cache["zeit"] = time.monotonic()
            _spitzen_cache["wert"] = wert
        return wert


async def seite(tor, jetzt=None):
    """Alles, was die Knotenseite braucht - jeder Wert einzeln abgesichert.

    Faellt ein Aufruf aus, fehlt genau sein Abschnitt und die Vorlage zeigt
    dort einen Strich. Es wird nichts geschaetzt und nichts ersetzt.
    """
    netz, gegen, totale, kette, mining, forks, tips = await asyncio.gather(
        _sicher(tor, "getnetworkinfo"),
        _sicher(tor, "getpeerinfo"),
        _sicher(tor, "getnettotals"),
        _sicher(tor, "getblockchaininfo"),
        _sicher(tor, "getmininginfo"),
        _sicher(tor, "getdeploymentinfo"),
        _spitzen_holen(tor, time.monotonic()),
    )
    netz = netz if isinstance(netz, dict) else None
    kette = kette if isinstance(kette, dict) else None
    mining = mining if isinstance(mining, dict) else None

    hoehe = (kette or {}).get("blocks")
    kopfzeilen = (kette or {}).get("headers")
    # Dieselbe Begruendung wie in knoten.zustand(): initialblockdownload allein
    # genuegt nicht, es steht auch nach laengerem Stillstand noch auf false.
    holt_auf = bool((kette or {}).get("initialblockdownload")) or (
        isinstance(hoehe, int) and isinstance(kopfzeilen, int)
        and kopfzeilen - hoehe > 1)

    # relayfee und incrementalfee kommen in BTC je kvB. sat/vB ist die Einheit,
    # in der Gebuehren ueberall sonst genannt werden: *1e8 fuer sat, /1000 fuer
    # vB - zusammen mal 100.000.
    def sat_vb(wert):
        return (float(wert) * 100000.0) if isinstance(wert, (int, float)) else None

    return {
        # "erreichbar" ist bewusst nicht an EINEN Aufruf gebunden: solange
        # irgendeine Quelle antwortet, hat die Seite etwas zu zeigen.
        "erreichbar": any(x is not None for x in (netz, kette, mining)),
        "software": eigene_software((netz or {}).get("subversion")),
        "protokoll": (netz or {}).get("protocolversion"),
        "netzwerk_aktiv": (netz or {}).get("networkactive"),
        "warnungen": _liste((kette or {}).get("warnings")) or
                     _liste((netz or {}).get("warnings")),
        "kette": (kette or {}).get("chain"),
        "hoehe": hoehe,
        "kopfzeilen": kopfzeilen,
        "rueckstand": (kopfzeilen - hoehe
                       if isinstance(hoehe, int) and isinstance(kopfzeilen, int)
                       and kopfzeilen > hoehe else None),
        "holt_auf": holt_auf,
        "fortschritt": (kette or {}).get("verificationprogress"),
        "platte": _skaliert((kette or {}).get("size_on_disk"), "B"),
        "beschnitten": bool((kette or {}).get("pruned")),
        "prune_ab": (kette or {}).get("pruneheight"),
        "prune_ziel": _skaliert((kette or {}).get("prune_target_size"), "B"),
        "schwierigkeit": _skaliert((mining or {}).get("difficulty")),
        "hashrate": _skaliert((mining or {}).get("networkhashps"), "H/s"),
        "relaygebuehr": sat_vb((netz or {}).get("relayfee")),
        "erhoehungsgebuehr": sat_vb((netz or {}).get("incrementalfee")),
        "verkehr": verkehr(totale),
        "gegenstellen": (gegenstellen(gegen, (netz or {}).get("networks"), jetzt)
                         if gegen is not None else None),
        "spitzen": spitzen(tips),
        "softforks": softforks(forks, hoehe),
    }


# ------------------------------------------------------- Selbstpruefung
def _selbsttest():
    """Prueft gegen ERFUNDENE Daten - kein Knoten, kein Netz.

    Der wichtigste Fall zuerst: dass keine Adresse durchkommt.
    """
    fehler = []

    def pruefe(name, ist, soll):
        if ist != soll:
            fehler.append("%s: %r erwartet, %r bekommen" % (name, soll, ist))
            print("  FEHLER  %s" % name)
        else:
            print("  ok      %s" % name)

    rohe = [
        {"addr": "203.0.113.7:8333", "addrlocal": "198.51.100.4:8333",
         "addrbind": "10.0.0.2:8333", "network": "ipv4", "inbound": False,
         "connection_type": "outbound-full-relay", "pingtime": 0.032,
         "bytessent": 1000, "bytesrecv": 500, "version": 70016,
         "subver": "/Satoshi:29.0.0/", "conntime": 1000, "id": 7,
         "transport_protocol_type": "v2"},
        {"addr": "abcdefghij234567.onion:8333", "network": "onion",
         "inbound": True, "connection_type": "inbound", "pingtime": 0.480,
         "bytessent": 200, "bytesrecv": 900, "version": 70015,
         "subver": "/Satoshi:27.1.0(knots20240801)/", "conntime": 4000,
         "transport_protocol_type": "v1"},
        {"network": "i2p", "inbound": False, "connection_type": "block-relay-only",
         "pingtime": 0.100, "bytessent": 10, "bytesrecv": 10, "version": 70016,
         "subver": "/Satoshi:29.0.0/", "conntime": 2000,
         "transport_protocol_type": "v2"},
    ]

    print("Datenschutz: die Whitelist haelt")
    verboten = ("addr", "addrlocal", "addrbind", "id")
    durchgerutscht = sorted({s for e in saeubern(rohe) for s in e if s in verboten})
    pruefe("keine Adressfelder ueberleben saeubern()", durchgerutscht, [])
    text = repr(gegenstellen(rohe, jetzt=10000))
    pruefe("keine IP im Ergebnis", "203.0.113.7" in text, False)
    pruefe("keine Onion-Adresse im Ergebnis", ".onion" in text, False)

    g = gegenstellen(rohe, netze_roh=[
        {"name": "ipv4", "reachable": True, "limited": False, "proxy": "127.0.0.1:9050"},
        {"name": "cjdns", "reachable": True, "limited": False},
    ], jetzt=10000)
    print("\nGegenstellen")
    pruefe("Anzahl", g["anzahl"], 3)
    pruefe("eingehend", g["eingehend"], 1)
    pruefe("anonym gezaehlt (onion+i2p)", g["anonym"], 2)
    pruefe("nur Bloecke", g["nur_bloecke"], 1)
    pruefe("verschluesselt (v2)", g["verschluesselt"], 2)
    pruefe("Median-Ping", round(g["ping_mitte"], 1), 100.0)
    pruefe("stilles, aber erreichbares Netz", g["stille"], ["cjdns"])
    pruefe("aelteste Verbindung in Stunden", round(g["aelteste_stunden"], 1), 2.5)
    pruefe("Knots wird nicht als Core gezaehlt",
           sorted(s["name"] for s in g["software"]),
           ["Core 29.0", "Knots 27.1"])

    print("\nSkalierung und Formate")
    pruefe("6,68 TB", _skaliert(6680000000000, "B"),
           {"wert": 6.68, "einheit": "TB", "stellen": 2})
    pruefe("None bleibt None", _skaliert(None), None)
    # 9,1e20 H/s sind 910 EH/s - die Groessenordnung des Netzes im Sommer 2026.
    pruefe("Hashrate", _skaliert(9.1e20, "H/s")["einheit"], "EH/s")
    pruefe("Hashrate, eine Stufe hoeher", _skaliert(1.2e21, "H/s")["einheit"], "ZH/s")
    pruefe("Software-Name", software_name("/Satoshi:29.0.0/"), "Core 29.0")
    pruefe("eigener Knoten mit vollem Stand",
           eigene_software("/Satoshi:29.0.0/"), "Bitcoin Core 29.0.0")
    pruefe("eigener Knoten, Knots erkannt",
           eigene_software("/Satoshi:27.1.0(knots20240801)/"),
           "Bitcoin Knots 27.1.0")
    pruefe("unzerlegbare Angabe bleibt roh",
           eigene_software("etwas Fremdes"), "etwas Fremdes")
    pruefe("Steuerzeichen raus", _text_saeubern("/Satoshi:2\x079.0/"),
           "/Satoshi:29.0/")
    pruefe("Warnung als String", _liste("alte Fassung"), ["alte Fassung"])
    pruefe("Warnungen als Liste", _liste(["a", ""]), ["a"])

    print("\nKettenspitzen")
    s = spitzen([
        {"height": 900000, "hash": "0" * 48 + "aaaabbbbccccdddd",
         "branchlen": 0, "status": "active"},
        {"height": 899998, "hash": "0" * 48 + "1111222233334444",
         "branchlen": 1, "status": "valid-fork"},
        {"height": 899000, "hash": "0" * 48 + "5555666677778888",
         "branchlen": 2, "status": "headers-only"},
    ])
    pruefe("Anzahl", s["anzahl"], 3)
    pruefe("Zweige ohne die aktive Spitze", s["zweige"], 2)
    pruefe("hoechster Zweig zuerst", s["liste"][0]["hoehe"], 899998)
    pruefe("Kurzform zeigt das Ende, nicht die Nullen",
           s["liste"][0]["kurz"], "…1111222233334444")
    pruefe("unbekannter Zustand bekommt keinen Textschluessel",
           spitzen([{"status": "brandneu"}])["liste"][0]["stand_schluessel"], None)

    print("\nSoft-Forks")
    f = softforks({"deployments": {
        "taproot": {"type": "bip9", "active": True, "height": 709632,
                    "bip9": {"bit": 2, "status": "active"}},
        "segwit": {"type": "buried", "active": True, "height": 481824},
        "neuling": {"type": "bip9", "active": False, "bip9": {
            "bit": 5, "status": "started", "min_activation_height": 950000,
            "statistics": {"period": 2016, "threshold": 1815, "elapsed": 1000,
                           "count": 900, "possible": True}}},
    }}, hoehe=900000)
    pruefe("laufende Aktivierung erkannt",
           [e["name"] for e in f["laufend"]], ["neuling"])
    pruefe("Balkenbreite Signale/Schwelle", f["laufend"][0]["breite"], 49.6)
    pruefe("aktive nach Hoehe sortiert",
           [e["name"] for e in f["aktiv"]], ["segwit", "taproot"])
    pruefe("Abstand in Bloecken", f["aktiv"][1]["abstand"], 190368)
    pruefe("Erklaertext nur wo vorhanden", f["laufend"][0]["erklaerung"], None)
    pruefe("nicht alles aktiv", f["alle_aktiv"], False)

    print("\nVerkehr")
    v = verkehr({"totalbytessent": 6680000000000, "totalbytesrecv": 1000000000000,
                 "uploadtarget": {"target": 0, "bytes_left_in_cycle": 0}})
    pruefe("gibt mehr als er nimmt", v["gibt_mehr"], True)
    pruefe("Verhaeltnis", round(v["verhaeltnis"], 2), 6.68)
    pruefe("ohne Limit kein Balken", v["grenze"], None)
    v2 = verkehr({"totalbytessent": 10, "totalbytesrecv": 100,
                  "uploadtarget": {"target": 1000, "bytes_left_in_cycle": 250,
                                   "target_reached": False}})
    pruefe("Limit zu drei Vierteln aufgebraucht", v2["grenze_breite"], 75.0)
    pruefe("nimmt mehr als er gibt", v2["gibt_mehr"], False)

    print("\nLeere und kaputte Antworten")
    pruefe("keine Gegenstellen", gegenstellen([])["anzahl"], 0)
    pruefe("kein Median ohne Pings", gegenstellen([])["ping_mitte"], None)
    pruefe("getchaintips ausgefallen", spitzen(None), None)
    pruefe("getdeploymentinfo ausgefallen", softforks(None), None)
    pruefe("getnettotals ausgefallen", verkehr(None), None)
    pruefe("Muell statt Gegenstellen", gegenstellen(["Unsinn", None])["anzahl"], 0)

    print("\n%d Fehler" % len(fehler))
    for f in fehler:
        print("  " + f)
    return 1 if fehler else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selbsttest())
