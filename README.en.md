# `openbanking-mcp` — read your accounts, and warn you BEFORE the debit lands

[![tests](https://github.com/Beennnn/openbanking-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/Beennnn/openbanking-mcp/actions/workflows/tests.yml)
[![licence MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
[![python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![zéro dépendance](https://img.shields.io/badge/d%C3%A9pendances-0-blue.svg)](pyproject.toml)

> **The repo is `openbanking-mcp`, the command is `bankread`.** The repo name says what
> this is — an MCP server on top of Open Banking — because that is how people find it.
> The command name says what you do with it, and `bankread doctor` types better than
> `openbanking-mcp doctor`. Config paths follow the command, not the repo.

Read-only access to your own bank accounts over PSD2 (AIS licence), plus a daily brief.
Python 3.11+, **no third-party dependencies**: `urllib` for HTTP, hand-written JSON-RPC
for the MCP server. Runs on your machine, not in anyone's cloud.

*(The full documentation is in French, in [`README.md`](README.md). This page is the
short version for people deciding whether it is worth their time.)*

## What it does that your bank does not

Your bank already tells you "your balance is low". It tells you on the 15th, after the
tax debit cleared that morning. It is accurate and useless.

The missing piece is not the measurement, it is the **subtraction**: today's balance
minus everything due before the end of the month. Neither your bank nor the tax office
can do it — neither one sees the other.

## The rule the whole repo is built on

**No green line that was not observed.** Every answer carries a state: `observe` (just
read), `ancien` (served from cache, with its age in plain text), `inconnu` (nothing fresh
enough to claim anything). A missing balance is never rendered as zero — that is the
worst possible reading of a missing number, and the MCP tool descriptions repeat it to
the model before it ever calls.

Same discipline on predictions: a charge seen twice is a coincidence, not a recurrence.
It ships as `confidence: "faible"` and stays out of the projection. Three regular
occurrences make a fact.

## See what it does, right now

```bash
./bankread demo
```

An invented account, 400 days of fabricated history, an imaginary balance — and the
**real** detection and projection running on top. Nothing is written outside a temp
directory.

```
    2026-08-23 ±3j    -890.00  →     394.55   Rent …           [loyer]
    2026-08-25 ±3j    -412.00  →     -17.45   Tax …            [impots]
    2026-08-29 ±3j    2450.00  →    2432.55   Salary …

  ⚠ drops below 300 € on 2026-08-25 (-17.45 €), pushed by « Dgfip Impot Revenu »
    (1 uncertain pattern not counted — the real trajectory may be lower)
```

Rent still leaves you above the floor; **the tax debit is what pushes you under**, five
days before payday. Your bank will mention it on the morning of the 25th. And the last
line matters most: the annual property tax was only seen twice in the history, so it is
not believed, so it does not count — and the projection says it is therefore optimistic
rather than pretending otherwise.

## Install, or don't

```bash
git clone https://github.com/Beennnn/openbanking-mcp && cd openbanking-mcp && ./bankread doctor
uvx --from git+https://github.com/Beennnn/openbanking-mcp bankread doctor   # nothing installed
pipx install git+https://github.com/Beennnn/openbanking-mcp                 # for good
```

The clone comes first on purpose: a tool that reads bank accounts gets read before it
gets installed. `./bankread` works straight out of a clone, with nothing installed at all.

## Getting started

1. **Create an Enable Banking application** — <https://enablebanking.com/sign-in/>.
   Self-serve, free at the *Restricted Production* tier, which serves real production
   data for the accounts you declare yourself. No company and no eIDAS certificate at
   that tier. The app id and RSA private key are yours alone: there is nothing to share,
   and this step cannot be skipped or done for you.
   Redirect URL to whitelist, exactly: `http://127.0.0.1:8788/callback`
2. `bankread secrets --set` — stored in the macOS keychain, never on disk.
3. `bankread banks <pattern>` then `bankread link "<exact bank name>"`.
4. `bankread doctor` and `bankread project --days 45 --floor 300`.

`link` chains straight into a deep backfill: full history is only served for about an
hour after consent is signed, after which most banks fall back to a rolling 90 days.

## Use it from any AI

`bankread mcp` is an MCP server over stdio — six read-only tools:

```json
{ "mcpServers": { "bankread": { "command": "/path/to/bankread", "args": ["mcp"] } } }
```

For Claude Code: `claude mcp add bankread -s user -- /path/to/bankread mcp`.

Or with no clone at all, the way MCP servers usually ship:

```json
{
  "mcpServers": {
    "bankread": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Beennnn/openbanking-mcp", "bankread", "mcp"]
    }
  }
}
```

MCP is an open protocol and the server is hand-written JSON-RPC: no vendor library, no
token, no outbound call. Anything that is not MCP can read `bankread json` instead. And
`BANKREAD_AGENT=aucun brief/run-brief` runs the whole brief with no model at all — the
subtraction never needed one. See [`docs/integration.md`](docs/integration.md).

## What it will never do

- **No bank writes.** The licence is AIS (account information); payment initiation is a
  separate licence this token does not have and the bank will not grant it. The worst
  case of a leak here is a readable history, never a moving euro.
- **No secrets on disk.** macOS keychain, private key included.
- **No scraping.** It breaks the terms of service, it breaks at the first redesign, and
  above all it puts a full password — the power to move money — inside an automated loop.
- **The brief never replies to anyone.** It reads mail; no Gmail write tool is in its
  closed tool list.

One thing it does keep on disk, and does not hide: `~/.local/share/bankread/ledger/`
accumulates up to a year of transactions in the clear, mode 0600. That is deliberate —
the bank forgets past 90 days, and an annual charge is undetectable without it.

## Tests

```bash
python3 test_bankread.py
```

51 tests, standard library only, no network. They mostly check the cases where the code
must stay silent.

## Licence

[MIT](LICENSE) — do what you want with it; keep the notice.
