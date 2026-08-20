"""Les deux erreurs que tout fournisseur de données doit savoir lever.

Elles vivent ici, et pas dans le client d'un fournisseur, pour une raison précise :
`read.py` doit pouvoir les attraper sans savoir à qui il parle. Tant qu'elles étaient
définies dans `gocardless.py`, `read.py` importait GoCardless — donc le dépôt entier
connaissait le fournisseur, alors que le README promettait le contraire.

La distinction entre les deux est le seul détail qui compte ici : `RateLimited` n'est PAS
une panne, c'est « repasse plus tard ». La bonne réponse est de servir le cache en disant
son âge ; la bonne réponse à `ApiError` est de dire qu'on ne sait pas. Les confondre
produirait exactement la ligne verte non observée que CLAUDE.md interdit.
"""

from __future__ import annotations

from typing import Any


class ApiError(RuntimeError):
    """Un appel a échoué. `status` porte le code HTTP quand il y en a un."""

    def __init__(self, message: str, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class RateLimited(ApiError):
    """Quota épuisé pour ce compte. `reset_seconds` dit dans combien de temps.

    Les banques plafonnent le rapatriement en arrière-plan (quatre appels par jour et par
    compte chez la plupart). Ce n'est pas une limite qu'on frôle, c'est une limite qu'on
    atteint en une matinée de mise au point — d'où le cache, qui n'est pas un confort.
    """

    def __init__(self, message: str, reset_seconds: int | None = None):
        super().__init__(message, status=429)
        self.reset_seconds = reset_seconds
