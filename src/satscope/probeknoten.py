"""Ein erfundener Bitcoin-Knoten fuer Tests.

Warum das noetig ist: Der einzige echte Knoten im Haus ist Daniels produktiver
Umbrel-Node - dort liegt Geld, und ein unbedachter Aufruf kostet dort Sekunden
CPU. Entwickeln und Pruefen darf davon nicht abhaengen.

Die Antworten sind den echten, gemessenen Antworten nachgebildet (Bitcoin Core
31.0, Stand 15.08.2026) - gleiche Feldnamen, plausible Groessenordnungen. Wer
hier ein Feld ergaenzt, das der echte Knoten nicht liefert, betruegt sich
selbst: dann laeuft der Test, aber die App nicht.

    from satscope.probeknoten import ProbeTor
    tor = ProbeTor()
    await tor.ruf("getblockchaininfo")
"""
from .rpc import BILLIG, NichtErlaubt

SPITZE = 962618
ZEIT = 1786820000


def _blockstats(hoehe):
    """getblockstats - die Fundgrube. Werte schwanken mit der Hoehe, damit
    Tests nicht versehentlich auf Konstanten hereinfallen."""
    n = hoehe % 7
    return {
        "height": hoehe,
        "avgfee": 1200 + n * 90,
        "avgfeerate": 2 + n,
        "avgtxsize": 380 + n * 12,
        "blockhash": "%064x" % hoehe,
        "feerate_percentiles": [1 + n, 2 + n, 3 + n, 5 + n, 12 + n],
        "ins": 3000 + n * 40,
        "maxfee": 400000 + n * 1000,
        "maxfeerate": 120 + n,
        "medianfee": 1100 + n * 50,
        "medianfeerate": 2 + n,
        "mediantime": ZEIT - (SPITZE - hoehe) * 600,
        "minfee": 250,
        "minfeerate": 1,
        "outs": 5390 + n * 30,
        "subsidy": 312500000,
        "swtotal_size": 820000,
        "swtxs": 3100 + n * 20,
        "time": ZEIT - (SPITZE - hoehe) * 600,
        "total_out": 1234567890123,
        "total_size": 997896 - n * 900,
        "total_weight": 3991584,
        "totalfee": 4300000 + n * 10000,
        "txs": 3638 + n * 25,
        "utxo_increase": 5390,
        "utxo_increase_actual": 41,
        "utxo_size_inc": 412000,
    }


ANTWORTEN = {
    "getblockchaininfo": {
        "chain": "main", "blocks": SPITZE, "headers": SPITZE,
        "bestblockhash": "%064x" % SPITZE, "difficulty": 126411437451912.2,
        "time": ZEIT, "mediantime": ZEIT - 3000,
        "verificationprogress": 0.9999998, "initialblockdownload": False,
        "size_on_disk": 868000000000, "pruned": False,
    },
    "getmempoolinfo": {
        "loaded": True, "size": 28450, "bytes": 27999211,
        "usage": 142000000, "total_fee": 0.234, "maxmempool": 300000000,
        "mempoolminfee": 0.00001, "minrelaytxfee": 0.00001,
        "unbroadcastcount": 0,
    },
    "getmininginfo": {
        "blocks": SPITZE, "difficulty": 126411437451912.2,
        "networkhashps": 9.1e20, "pooledtx": 28450, "chain": "main",
    },
    "getnetworkinfo": {
        "version": 310000, "subversion": "/Satoshi:31.0.0/",
        "protocolversion": 70016, "connections": 24,
        "connections_in": 14, "connections_out": 10,
        "relayfee": 0.00001, "incrementalfee": 0.00001,
        # ⚠️ Die echten localaddresses enthalten die Onion- und I2P-Adresse des
        # Knotens. Sie stehen hier ABSICHTLICH drin, damit ein Test auffliegen
        # laesst, wenn eine Ansicht sie versehentlich anzeigt.
        "localaddresses": [{"address": "ims55lvexample000000000000000000000000000000000000000.b32.i2p",
                            "port": 0, "score": 4}],
        "networks": [{"name": "ipv4", "reachable": True},
                     {"name": "onion", "reachable": True},
                     {"name": "i2p", "reachable": True}],
    },
    "getnettotals": {
        "totalbytesrecv": 412000000000, "totalbytessent": 6680000000000,
        "timemillis": ZEIT * 1000,
    },
    "getchaintips": [
        {"height": SPITZE, "hash": "%064x" % SPITZE, "branchlen": 0, "status": "active"},
        {"height": 962601, "hash": "%064x" % 999001, "branchlen": 1, "status": "valid-fork"},
        {"height": 962550, "hash": "%064x" % 999002, "branchlen": 1, "status": "valid-headers"},
    ],
    "getdeploymentinfo": {
        "hash": "%064x" % SPITZE, "height": SPITZE,
        "deployments": {
            "taproot": {"type": "bip9", "active": True, "height": 709632},
            "testdummy": {"type": "bip9", "active": False,
                          "bip9": {"status": "started", "start_time": 0,
                                   "timeout": 0, "since": 960000,
                                   "statistics": {"period": 2016, "threshold": 1815,
                                                  "elapsed": 900, "count": 700,
                                                  "possible": True}}},
        },
    },
    "estimatesmartfee": {"feerate": 0.00002, "blocks": 1},
    "estimaterawfee": {"short": {"feerate": 0.000019, "decay": 0.962,
                                 "scale": 1, "pass": {"withintarget": 1200.0,
                                                      "totalconfirmed": 1180.0,
                                                      "inmempool": 30.0,
                                                      "leftmempool": 12.0}}},
}


def _peers():
    """getpeerinfo - je Gegenstelle. Enthaelt ABSICHTLICH echte Adressfelder,
    damit ein Test bemerkt, wenn eine Ansicht sie ausgibt."""
    netze = ["ipv4", "ipv4", "ipv6", "onion", "onion", "i2p", "cjdns"]
    raus = []
    for i in range(24):
        netz = netze[i % len(netze)]
        raus.append({
            "id": i, "addr": "203.0.113.%d:8333" % (i + 1),
            "addrbind": "192.168.178.67:8333",
            "network": netz, "inbound": i % 3 == 0,
            "pingtime": 0.02 + i * 0.004, "minping": 0.018 + i * 0.003,
            "version": 70016, "subver": "/Satoshi:2%d.0.0/" % (5 + i % 6),
            "bytessent": 1000000 * (i + 1), "bytesrecv": 900000 * (i + 1),
            "conntime": ZEIT - 86400 * (i % 30 + 1),
            "transport_protocol_type": "v2" if i % 2 else "v1",
        })
    return raus


class ProbeTor:
    """Verhaelt sich wie rpc.Tor, antwortet aber aus der Tabelle oben.

    Weist teure Methoden genauso ab wie das echte Tor - sonst wuerde ein Test
    Code durchwinken, der im Betrieb den Knoten des Nutzers lahmlegt.
    """

    def __init__(self, erlaubt=BILLIG, ausfall=()):
        self.erlaubt = frozenset(erlaubt)
        self.ausfall = set(ausfall)      # Methoden, die scheitern sollen
        self.aufrufe = []                # zum Nachzaehlen im Test

    def kennt(self, methode):
        return methode in self.erlaubt

    async def ruf(self, methode, *argumente):
        self.aufrufe.append((methode, argumente))
        if methode not in self.erlaubt:
            raise NichtErlaubt("%s ist in diesem Tor nicht erlaubt" % methode)
        if methode in self.ausfall:
            from .rpc import RpcFehler
            raise RpcFehler("%s (im Test absichtlich ausgefallen)" % methode)

        if methode == "getblockhash":
            hoehe = argumente[0] if argumente else SPITZE
            if not 0 <= hoehe <= SPITZE:
                from .rpc import RpcFehler
                raise RpcFehler("Block height out of range")
            return "%064x" % hoehe
        if methode == "getblockheader":
            return {"hash": argumente[0] if argumente else "%064x" % SPITZE,
                    "height": SPITZE, "time": ZEIT, "mediantime": ZEIT - 3000,
                    "nonce": 123456789, "bits": "17030ecd",
                    "difficulty": 126411437451912.2, "confirmations": 1,
                    "merkleroot": "%064x" % 42,
                    "previousblockhash": "%064x" % (SPITZE - 1),
                    "nTx": 3638, "version": 536870912}
        if methode == "getblockstats":
            ziel = argumente[0] if argumente else SPITZE
            return _blockstats(ziel if isinstance(ziel, int) else SPITZE)
        if methode == "getpeerinfo":
            return _peers()
        if methode == "getrawtransaction":
            return {"txid": argumente[0] if argumente else "%064x" % 7,
                    "hash": "%064x" % 8, "size": 380, "vsize": 220,
                    "weight": 880, "version": 2, "locktime": 0,
                    "fee": 0.0000044, "confirmations": 3,
                    "blockhash": "%064x" % SPITZE, "time": ZEIT,
                    "vin": [{"txid": "%064x" % 5, "vout": 0, "sequence": 4294967293,
                             "prevout": {"value": 0.01, "scriptPubKey":
                                         {"type": "witness_v0_keyhash",
                                          "address": "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"}}}],
                    "vout": [{"value": 0.009, "n": 0, "scriptPubKey":
                              {"type": "witness_v1_taproot", "address":
                               "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"}}]}
        if methode == "getmempoolentry":
            return {"vsize": 220, "fees": {"base": 0.0000044, "ancestor": 0.0000044,
                                           "descendant": 0.0000044},
                    "time": ZEIT - 4800, "depends": [], "spentby": [],
                    "bip125-replaceable": True, "ancestorcount": 1,
                    "descendantcount": 1}
        if methode == "gettxspendingprevout":
            return [{"txid": "%064x" % 5, "vout": 0}]
        if methode == "getmempoolcluster":
            return {"txcount": 3, "vsize": 640, "fee": 0.0000121}
        return ANTWORTEN.get(methode, {})
