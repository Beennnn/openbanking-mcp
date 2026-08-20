"""Le registre : l'historique qu'on ACCUMULE, parce que la banque, elle, oublie.

Ajouté le 2026-08-20, en préparant le branchement réel sur BoursoBank — et c'est ce
branchement qui a révélé le trou.

BoursoBank ne rend qu'environ **90 jours** d'opérations par l'API DSP2. Le cache écrit
ce matin était un instantané : chaque lecture remplaçait la précédente. Conséquence que
personne n'aurait vue avant un an — une échéance ANNUELLE (taxe foncière, prime
d'assurance, redevance) n'aurait JAMAIS été détectée. Pas « pas encore » : jamais. À
chaque passage, tout ce qui dépassait 90 jours disparaissait de l'API *et* du cache en
même temps, donc `recurring.detecter()` ne pouvait par construction rien voir au-delà
d'un trimestre. Le tableau serait resté vert et faux, ce que CLAUDE.md interdit.

D'où ce fichier. Le registre est **cumulatif et durable** : chaque lecture y est fondue,
rien n'en sort. Au bout d'un an de briefs quotidiens, bankread connaît 365 jours
d'historique là où la banque n'en montre que 90 — et la taxe foncière devient visible
au deuxième passage, comme n'importe quelle autre échéance.

⚠️ **Le registre n'est PAS un cache et ne doit jamais être purgé avec lui.** Ce qu'il
contient, la banque ne peut plus le redonner. Le supprimer, c'est repartir à 90 jours de
mémoire et perdre des mois d'accumulation. Il vit donc dans ~/.local/share (données),
pas dans ~/.cache (jetable).

Contrepartie honnête : c'est un an d'opérations bancaires en clair sur le disque. Le
fichier est en 0600 et ne quitte pas la machine, mais ce n'est pas rien, et c'est
dit ici plutôt que découvert plus tard.

Sur la déduplication : `internalTransactionId` existe chez certaines banques et pas
chez d'autres, et il est rapporté comme instable d'une lecture à l'autre. On ne s'y fie
donc pas. La clé est le CONTENU — (date, montant, libellé) — et le comptage se fait par
jour : deux prélèvements Spotify identiques le même jour restent deux lignes, parce
qu'on retient `max(déjà connu, vu maintenant)` pour chaque triplet, jamais leur somme.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path


def cle(ligne: dict) -> tuple:
    """Ce qui identifie une opération, indépendamment de l'identifiant de la banque."""
    jour = ligne["date"]
    return (
        jour.isoformat() if isinstance(jour, date) else str(jour)[:10],
        round(float(ligne["montant"]), 2),
        " ".join(str(ligne.get("libelle", "")).split()),
    )


def fusionner(connues: list[dict], lues: list[dict]) -> tuple[list[dict], int]:
    """(registre à jour, nombre de lignes vraiment nouvelles).

    `max` par triplet, et non addition : relire deux fois la même fenêtre ne doit rien
    dupliquer, alors qu'un deuxième prélèvement réellement identique le même jour doit
    bien apparaître deux fois. L'addition ferait doubler tout l'historique à chaque
    brief ; garder la seule valeur connue ferait disparaître le doublon légitime.

    Les jours absents de la lecture ne sont pas touchés : c'est ce qui permet à la
    fenêtre glissante de la banque d'avancer sans emporter le passé avec elle.
    """
    compte_connu = Counter(cle(l) for l in connues)
    compte_lu = Counter(cle(l) for l in lues)

    par_cle: dict[tuple, dict] = {}
    for l in list(connues) + list(lues):
        par_cle.setdefault(cle(l), _normaliser(l))

    sortie: list[dict] = []
    nouvelles = 0
    for k in set(compte_connu) | set(compte_lu):
        combien = max(compte_connu[k], compte_lu[k])
        nouvelles += max(0, compte_lu[k] - compte_connu[k])
        sortie.extend(dict(par_cle[k]) for _ in range(combien))

    sortie.sort(key=lambda l: (l["date"], -abs(l["montant"]), l["libelle"]))
    return sortie, nouvelles


def profondeur_jours(registre: list[dict], aujourdhui: date | None = None) -> int:
    """Combien de jours d'historique le registre porte réellement."""
    if not registre:
        return 0
    aujourdhui = aujourdhui or date.today()
    plus_vieille = min(date.fromisoformat(l["date"]) for l in registre)
    return max(0, (aujourdhui - plus_vieille).days)


def charger(chemin: Path) -> list[dict]:
    try:
        blob = json.loads(chemin.read_text())
    except (OSError, ValueError):
        return []
    lignes = blob.get("transactions", []) if isinstance(blob, dict) else blob
    return [l for l in lignes if isinstance(l, dict) and "date" in l]


def enregistrer(chemin: Path, registre: list[dict]) -> None:
    """Écriture atomique : un brief qui lit pendant l'écriture voit l'ancien fichier
    entier, jamais un demi-registre — et un demi-registre, ici, ce serait de l'historique
    perdu pour de bon."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"transactions": registre}, ensure_ascii=False))
    tmp.replace(chemin)
    try:
        chemin.chmod(0o600)
    except OSError:
        pass


def _normaliser(ligne: dict) -> dict:
    jour = ligne["date"]
    return {
        "date": jour.isoformat() if isinstance(jour, date) else str(jour)[:10],
        "montant": round(float(ligne["montant"]), 2),
        "libelle": " ".join(str(ligne.get("libelle", "")).split()),
    }
