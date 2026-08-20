"""bankread — lecture seule des comptes bancaires, via l'agrément DSP2 de GoCardless.

  gocardless.py parle à GoCardless (jetons, quotas, HTTP) — UN fournisseur parmi d'autres
  store.py      range (trousseau macOS, ~/.config/bankread, cache daté)
  ledger.py     ACCUMULE l'historique, parce que la banque n'en garde que 90 jours
  recurring.py  retrouve les échéances qui reviennent et projette le solde
  read.py       décide quoi servir quand la banque ne répond pas — la seule politique
  mcp.py        expose six outils de lecture à Claude, en JSON-RPC sur stdio

Rien ici ne peut déplacer d'argent : le jeton obtenu porte le scope AIS
(balances/details/transactions) et la banque ne lui ouvre pas l'initiation de paiement.
"""

__all__ = ["gocardless", "store", "recurring", "read", "mcp"]
