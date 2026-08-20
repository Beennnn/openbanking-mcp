"""Qui va chercher la donnée — et le contrat que le reste du dépôt attend de lui.

Ce fichier existe pour que `read.py`, `mcp.py` et le brief n'aient JAMAIS à nommer un
fournisseur. Avant lui, `read.py` importait `gocardless` directement : le dépôt entier
connaissait donc son fournisseur, alors que le README promettait qu'un seul fichier le
connaissait. La promesse était fausse, et elle n'aurait été démentie qu'au moment le plus
coûteux — celui du remplacement en catastrophe.

Le contrat tient en deux méthodes, écrit ici en `Protocol` plutôt qu'en prose : un
fournisseur est n'importe quel objet qui sait rendre des soldes et des opérations au
format du Groupe de Berlin. Rien d'autre n'est exigé de lui, et rien d'autre ne doit
l'être — c'est ce qui a permis d'écrire le client Enable Banking sans toucher à la
détection d'échéances ni à la projection.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .erreurs import ApiError, RateLimited

__all__ = ["Fournisseur", "ApiError", "RateLimited", "charger", "NOMS"]

NOMS = {
    "gocardless": "GoCardless Bank Account Data",
    "enablebanking": "Enable Banking",
}


@runtime_checkable
class Fournisseur(Protocol):
    """Ce que `read.py` a le droit de demander, et rien de plus.

    Les deux méthodes rendent le format du Groupe de Berlin : `balanceType` /
    `balanceAmount` pour les soldes, `bookingDate` / `transactionAmount` **signé** pour
    les opérations. C'est au client du fournisseur de traduire s'il parle autre chose —
    voir `enablebanking._operation()`, qui rend leur signe aux montants.
    """

    def balances(self, account_id: str) -> dict: ...

    def transactions(self, account_id: str, date_from: str | None = None,
                     date_to: str | None = None) -> dict: ...


def charger(store, presence_humaine: bool = False) -> Fournisseur:
    """Le client du fournisseur configuré. Aucun appel réseau ici.

    `presence_humaine` est transmis tel quel : il dit à la banque qu'un utilisateur est
    réellement devant l'écran, ce qui lève le plafond du rapatriement en arrière-plan.
    C'est une déclaration, pas un réglage de performance — le brief de 7 h 30 doit la
    laisser à False.
    """
    nom = store.fournisseur()
    if nom == "enablebanking":
        from .enablebanking import Api as ApiEnableBanking
        return ApiEnableBanking(store, presence_humaine=presence_humaine)
    from .gocardless import Api as ApiGoCardless
    return ApiGoCardless(store)
