# Satscope

**Every number in context.**

A Bitcoin block explorer for your own node. It looks as plain as the explorer you
already know — and tells you, in whole sentences, what actually happened in a
block, a transaction or an address.

Runs as a standalone [Umbrel](https://umbrel.com) app. No outbound calls to the
internet: everything you see comes from your own Bitcoin Core and Electrum server.

---

## Status

Early construction. Nothing here is usable yet — see the roadmap below.

## The idea

Most explorers answer "what is the number?". Satscope answers "is that number
unusual?". `1.00 sat/vB` is worthless on its own; *"1.00 sat/vB — the lowest in
three days"* is a statement.

Three rules keep it from turning into another wall of dashboards:

1. **The front page never grows.** It answers one question: how is the network
   doing right now? Every new capability lives on the object it belongs to.
2. **Every number names its frame of reference.** Compared to what, over which
   period.
3. **Nothing is invented.** If a source is unavailable, Satscope shows a dash —
   never a guess.

If a feature needs a new route or a menu entry, it is designed wrong.

## Five doors, no menu

| Route | Question it answers |
|---|---|
| `/` | How is the network doing right now? |
| `/block/<id>` | What happened in this block? |
| `/tx/<txid>` | What is going on with this transaction? |
| `/address/<addr>` | What belongs to this address? |
| `/node` | How is your own node doing? |

## Languages

English and German, switchable. English is the default. The choice is stored per
browser in a cookie, so the server can render the right language on first paint.

## Roadmap

- **Stage A** — the familiar explorer: blocks, transactions, addresses, search,
  live updates via ZMQ. No index required.
- **Stage B** — the reason this project exists: block x-ray (coin days destroyed,
  RBF share, script types, OP_RETURN by protocol), real-time replacement ticker,
  address reuse per block, mempool map with Core 31 chunks, miner audit against
  block templates, block arrival timing, xpub search.
- **Stage C** — `coinstatsindex`, throttled historical backfill, UTXO age
  distribution, alerts, onion access.

## Licence

MIT — see [LICENSE](LICENSE).

## Building and releasing

The image is built by GitHub Actions on every push to `main` — multi-arch
(`linux/amd64` + `linux/arm64`), published to `ghcr.io/saftcpu/satscope`,
exactly the way other Umbrel apps ship.

No local Docker is needed, and no token: Actions issues its own `GITHUB_TOKEN`
per run.

After the very first successful run, the package must be switched to **public**
once — GitHub creates container packages private by default, and Umbrel could
not pull it:

    github.com/users/SaftCPU/packages/container/satscope/settings
      -> Change visibility -> Public

Then pin the digest of the **manifest list** (not of a single architecture) in
`satscope/docker-compose.yml`. The Actions run summary prints it.
