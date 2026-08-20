#!/bin/bash
# Pilote « aucun » — le brief SANS aucune IA.
#
# Il existe pour prouver une propriété du dépôt plutôt que pour remplacer le brief : la
# lecture des comptes et la soustraction ne dépendent d'aucun modèle. Si Claude est en
# panne, si le quota est épuisé, si l'API a changé d'avis, la seule chose qui comptait —
# « le solde de la fin du mois est-il négatif ? » — continue de tomber dans le journal.
#
# CE QU'IL NE FAIT PAS, et il ne faut pas le lui demander : lire le courrier, reconnaître
# un colis en retard, écrire une tâche Todoist. Tout ça suppose de comprendre du texte
# libre, et c'est exactement ce pour quoi le modèle est là.
#
# Le code de sortie est celui de `bankread project` : 0 tout va bien, 1 il y a à
# regarder, 2 échec dur — launchd et les scripts appelants s'y retrouvent.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
exec "$REPO/bankread" project --days "${BANKREAD_JOURS:-45}" --floor "${BANKREAD_PLANCHER:-0}"
