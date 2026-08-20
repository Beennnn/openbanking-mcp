# `bankread` — read your accounts, and warn you BEFORE the debit lands

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
