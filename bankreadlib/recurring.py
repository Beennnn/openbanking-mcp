"""Retrouver les échéances qui reviennent, et projeter le solde devant soi.

C'est LA raison d'être de ce dossier. Une alerte de seuil bancaire — toutes les banques
en envoient — arrive quand le mal est fait : « votre solde est bas » le 15, alors
que le prélèvement des impôts est passé le matin même. Elle est exacte et inutile.

Ce qui manque n'est pas la mesure, c'est la SOUSTRACTION : le solde d'aujourd'hui moins
ce qui va tomber d'ici la fin du mois. Personne ne la fait, ni la banque ni les impôts,
parce qu'aucun des deux ne voit l'autre.

Deux choix qui décident de la justesse du résultat :

1. **Les rentrées comptent autant que les sorties.** Projeter les seuls prélèvements
   donne une trajectoire qui plonge toujours, donc une alarme tous les jours, donc plus
   d'alarme du tout au bout d'une semaine. Le salaire, les allocations, les loyers perçus
   sont détectés par le même chemin et signés dans l'autre sens.

2. **Le doute se dit.** Une échéance vue deux fois n'est pas une échéance, c'est une
   coïncidence ; on la marque `confidence: "faible"` et l'appelant décide. Trois
   occurrences régulières, c'est un fait. C'est la règle de CLAUDE.md appliquée à une
   prédiction : ne jamais rendre un vert qu'on n'a pas observé.

Ce qui n'est PAS tenté ici, volontairement : deviner les montants variables (électricité,
carte de crédit). On prend la médiane des passages connus et on signale la dispersion.
Une régression sur douze points bruités donnerait un chiffre plus précis et plus faux.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

# Un motif doit s'être répété au moins deux fois pour exister, trois pour être cru.
MIN_OCCURRENCES = 2
CONFIANT_A_PARTIR_DE = 3

# Les cadences reconnues, en jours, avec leur tolérance. La mensuelle est large (26-33)
# parce qu'un prélèvement au 31 tombe au 28 en février et qu'un week-end le décale au
# lundi : le même prélèvement peut afficher 26 jours d'écart puis 33.
CADENCES = (
    ("hebdomadaire", 7, 2),
    ("mensuelle", 30, 4),
    ("bimestrielle", 61, 6),
    ("trimestrielle", 91, 8),
    ("semestrielle", 182, 12),
    ("annuelle", 365, 20),
)

# Étiquettes bancaires : tout ce qui identifie l'OPÉRATION plutôt que le CRÉANCIER.
# Sans ce ménage, « PRLV SEPA EDF 12/07 REF 88213 » et « PRLV SEPA EDF 12/08 REF 88907 »
# sont deux créanciers différents et plus rien ne se répète jamais.
_BRUIT = re.compile(
    r"\b(prlv|prelevement|prelvt|sepa|vir|virement|inst|instantane|recu|emis|"
    r"carte|cb|achat|paiement|facture|ech|echeance|mandat|rum|ics|ref|no|num)\b",
    re.IGNORECASE,
)
_CHIFFRES = re.compile(r"\d+")
_PONCTUATION = re.compile(r"[^A-Z ]+")
_ESPACES = re.compile(r"\s+")

# Familles reconnues à la lecture du libellé. Sert au brief : « impôts » mérite une tâche
# nommée, « SPOTIFY » non. Volontairement court — une taxonomie complète se périme.
_FAMILLES = (
    ("impots", r"\b(dgfip|d\.?g\.?f\.?i\.?p|finances publiques|tresor public|impot|"
               r"direction generale des finances)\b"),
    ("energie", r"\b(edf|engie|total ?energies|enedis|grdf|eni|ekwateur)\b"),
    ("telecom", r"\b(free|orange|sfr|bouygues|sosh|red by sfr)\b"),
    ("assurance", r"\b(axa|maif|macif|matmut|maaf|allianz|gmf|mutuelle|smi|harmonie)\b"),
    ("credit", r"\b(pret|credit|emprunt|echeance de pret)\b"),
    ("loyer", r"\b(loyer|bail|syndic|charges de copro)\b"),
)


@dataclass
class Echeance:
    """Une opération qui revient. Montant positif = rentrée, négatif = sortie."""

    libelle: str
    montant: float
    cadence: str
    jours_entre: float
    occurrences: int
    derniere: date
    prochaine: date
    confidence: str
    famille: str = ""
    dispersion: float = 0.0          # écart-type des montants, en euros
    incertitude_jours: int = 2       # décalage plausible (week-ends, jours fériés)
    exemples: list[str] = field(default_factory=list)

    @property
    def sortie(self) -> bool:
        return self.montant < 0

    def as_dict(self) -> dict:
        return {
            "libelle": self.libelle,
            "montant": round(self.montant, 2),
            "sens": "sortie" if self.sortie else "rentree",
            "cadence": self.cadence,
            "occurrences": self.occurrences,
            "derniere": self.derniere.isoformat(),
            "prochaine": self.prochaine.isoformat(),
            "incertitude_jours": self.incertitude_jours,
            "confidence": self.confidence,
            "famille": self.famille,
            "dispersion_euros": round(self.dispersion, 2),
            "exemples": self.exemples[:3],
        }


def normaliser(transactions: list[dict]) -> list[dict]:
    """Format du Groupe de Berlin → {date, montant, libelle}. Ignore ce qui n'a pas de date.

    `bookingDate` plutôt que `valueDate` : c'est la date à laquelle l'argent a bougé sur
    le compte, donc celle qui se répète à cadence fixe. La date de valeur glisse selon
    l'interbancaire et brouillerait la détection.
    """
    out = []
    for t in transactions or []:
        jour = t.get("bookingDate") or t.get("valueDate")
        if not jour:
            continue
        try:
            d = date.fromisoformat(str(jour)[:10])
        except ValueError:
            continue
        try:
            montant = float(t.get("transactionAmount", {}).get("amount", ""))
        except (TypeError, ValueError):
            continue
        libelle = (
            t.get("creditorName")
            or t.get("debtorName")
            or t.get("remittanceInformationUnstructured")
            or " ".join(t.get("remittanceInformationUnstructuredArray") or [])
            or "?"
        )
        out.append({"date": d, "montant": montant, "libelle": str(libelle).strip()})
    return out


def cle(libelle: str) -> str:
    """Réduire un libellé à ce qui identifie le créancier."""
    sans_accent = "".join(
        c for c in unicodedata.normalize("NFD", libelle) if unicodedata.category(c) != "Mn"
    )
    s = _CHIFFRES.sub(" ", sans_accent.upper())
    s = _PONCTUATION.sub(" ", s)
    s = _BRUIT.sub(" ", s)
    return _ESPACES.sub(" ", s).strip()


def famille(libelle: str) -> str:
    bas = cle(libelle).lower()
    for nom, motif in _FAMILLES:
        if re.search(motif, bas):
            return nom
    return ""


def detecter(transactions: list[dict], aujourdhui: date | None = None) -> list[Echeance]:
    """Les échéances récurrentes visibles dans l'historique, les plus proches d'abord."""
    aujourdhui = aujourdhui or date.today()
    groupes: dict[str, list[dict]] = {}
    for t in transactions:
        k = cle(t["libelle"])
        if len(k) < 3:
            continue  # un libellé réduit à deux lettres ne distingue plus rien
        groupes.setdefault(k, []).append(t)

    echeances = []
    for k, items in groupes.items():
        items.sort(key=lambda x: x["date"])
        # Deux prélèvements le même jour sont un seul événement vu deux fois (frais
        # séparés du principal) : les fusionner évite une cadence fantôme de 0 jour.
        fusionnes = _fusionner_meme_jour(items)
        if len(fusionnes) < MIN_OCCURRENCES:
            continue
        ech = _echeance_du_groupe(k, fusionnes, aujourdhui)
        if ech:
            echeances.append(ech)

    echeances.sort(key=lambda e: (e.prochaine, -abs(e.montant)))
    return echeances


def _fusionner_meme_jour(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for t in items:
        if out and out[-1]["date"] == t["date"]:
            out[-1] = {**out[-1], "montant": out[-1]["montant"] + t["montant"]}
        else:
            out.append(dict(t))
    return out


def _echeance_du_groupe(k: str, items: list[dict], aujourdhui: date) -> Echeance | None:
    dates = [t["date"] for t in items]
    ecarts = [(b - a).days for a, b in zip(dates, dates[1:])]
    if not ecarts:
        return None

    ecart_median = _mediane(ecarts)
    cadence = _nommer_cadence(ecart_median)
    if not cadence:
        return None

    montants = [t["montant"] for t in items]
    # Les rentrées et les sorties ne se mélangent pas sous un même libellé : un
    # remboursement ponctuel ne doit pas annuler la moitié d'un abonnement.
    signe = 1 if _mediane(montants) >= 0 else -1
    montants = [m for m in montants if (m >= 0) == (signe > 0)]
    if len(montants) < MIN_OCCURRENCES:
        return None

    montant = _mediane(montants)
    derniere = dates[-1]
    prochaine = _prochaine(derniere, dates, cadence, ecart_median, aujourdhui)

    occurrences = len(items)
    regulier = _regularite(ecarts, ecart_median)
    confidence = "sure" if (occurrences >= CONFIANT_A_PARTIR_DE and regulier) else "faible"

    return Echeance(
        libelle=k.title(),
        montant=montant,
        cadence=cadence,
        jours_entre=ecart_median,
        occurrences=occurrences,
        derniere=derniere,
        prochaine=prochaine,
        confidence=confidence,
        famille=famille(items[-1]["libelle"]),
        dispersion=_ecart_type(montants),
        incertitude_jours=3 if cadence in ("mensuelle", "bimestrielle") else 5,
        exemples=[t["libelle"] for t in items[-3:]],
    )


def _nommer_cadence(ecart_median: float) -> str:
    for nom, cible, tolerance in CADENCES:
        if abs(ecart_median - cible) <= tolerance:
            return nom
    return ""


def _regularite(ecarts: list[int], median: float) -> bool:
    """Vrai si tous les écarts tiennent dans ±25 % de la médiane.

    Un abonnement mensuel dérive de quelques jours (week-ends) mais pas de deux semaines.
    Le seuil relatif marche pour l'annuel comme pour l'hebdomadaire.
    """
    if median <= 0:
        return False
    return all(abs(e - median) <= max(2.0, 0.25 * median) for e in ecarts)


def _prochaine(derniere: date, dates: list[date], cadence: str,
               ecart_median: float, aujourdhui: date) -> date:
    """La date du prochain passage, avancée jusqu'à dépasser aujourd'hui.

    Pour le mensuel on rejoue le JOUR DU MOIS habituel plutôt qu'un +30 jours : un loyer
    au 5 reste au 5, alors que l'addition de 30 jours le ferait dériver de six jours par
    semestre et finirait par annoncer les échéances dans le mauvais mois.
    """
    if cadence == "mensuelle":
        jour = int(_mediane([float(d.day) for d in dates]))
        candidat = _au_jour_du_mois(derniere, jour)
        while candidat <= aujourdhui:
            candidat = _au_jour_du_mois(_ajouter_mois(candidat, 1), jour)
        return candidat

    candidat = derniere + timedelta(days=round(ecart_median))
    while candidat <= aujourdhui:
        candidat += timedelta(days=round(ecart_median))
    return candidat


def _ajouter_mois(d: date, n: int) -> date:
    mois = d.month - 1 + n
    annee = d.year + mois // 12
    return date(annee, mois % 12 + 1, 1)


def _au_jour_du_mois(d: date, jour: int) -> date:
    """Le `jour` du mois de `d`, ramené au dernier jour quand le mois est trop court."""
    mois_suivant = _ajouter_mois(date(d.year, d.month, 1), 1)
    dernier = (mois_suivant - timedelta(days=1)).day
    return date(d.year, d.month, min(jour, dernier))


def projeter(solde: float, echeances: list[Echeance], jours: int = 45,
             plancher: float = 0.0, aujourdhui: date | None = None,
             inclure_faibles: bool = False) -> dict:
    """Dérouler le solde jour par jour et dire QUAND il passe sous le plancher.

    Rend la première date de franchissement, le point bas, et le détail des mouvements
    retenus — le détail compte autant que la conclusion : une alerte qu'on ne peut pas
    vérifier ne sert qu'une fois.
    """
    aujourdhui = aujourdhui or date.today()
    retenues = [e for e in echeances if inclure_faibles or e.confidence == "sure"]

    mouvements: list[tuple[date, Echeance]] = []
    for e in retenues:
        quand = e.prochaine
        jour_cible = e.prochaine.day
        # Une échéance peut repasser plusieurs fois dans la fenêtre (hebdomadaire sur
        # 45 jours) : on les déroule toutes, sinon la projection sous-estime.
        #
        # Le mensuel avance de MOIS en mois, pas de 30 jours — la même raison qu'en
        # _prochaine() : +30 j recule d'un jour par mois, et sur une projection de
        # 90 jours le loyer finit annoncé le 2 au lieu du 5.
        while (quand - aujourdhui).days <= jours:
            mouvements.append((quand, e))
            if e.cadence == "mensuelle":
                quand = _au_jour_du_mois(_ajouter_mois(quand, 1), jour_cible)
            else:
                quand = quand + timedelta(days=max(1, round(e.jours_entre)))
            if len(mouvements) > 500:
                break
    mouvements.sort(key=lambda m: m[0])

    courant = solde
    trajectoire = []
    franchissement = None
    point_bas = (aujourdhui, solde)
    for quand, e in mouvements:
        courant += e.montant
        trajectoire.append({
            "date": quand.isoformat(),
            "libelle": e.libelle,
            "montant": round(e.montant, 2),
            "famille": e.famille,
            "solde_apres": round(courant, 2),
            "incertitude_jours": e.incertitude_jours,
        })
        if courant < point_bas[1]:
            point_bas = (quand, courant)
        if franchissement is None and courant < plancher:
            franchissement = {"date": quand.isoformat(), "solde": round(courant, 2),
                              "declencheur": e.libelle}

    return {
        "solde_depart": round(solde, 2),
        "fenetre_jours": jours,
        "plancher": plancher,
        "franchissement": franchissement,
        "point_bas": {"date": point_bas[0].isoformat(), "solde": round(point_bas[1], 2)},
        "solde_projete_fin": round(courant, 2),
        "mouvements": trajectoire,
        "echeances_ignorees_car_incertaines": (
            0 if inclure_faibles else sum(1 for e in echeances if e.confidence != "sure")
        ),
    }


def _mediane(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _ecart_type(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    moyenne = sum(xs) / len(xs)
    return (sum((x - moyenne) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
