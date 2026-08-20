"""Un compte fictif, pour voir ce que fait l'outil sans avoir de banque branchée.

Le problème que ça règle est celui d'un dépôt public : entre le clone et la première
lecture réelle, il y a une inscription chez un fournisseur, une clé RSA à télécharger et
un consentement bancaire à signer sur le site de sa banque. Personne ne fait tout ça pour
« voir à quoi ça ressemble » — donc personne ne voit jamais la seule chose qui compte
ici, la soustraction.

`bankread demo` monte un compte entièrement inventé dans un dossier temporaire, y verse
400 jours d'historique fabriqué, et fait tourner LA VRAIE détection d'échéances et LA
VRAIE projection dessus. Rien n'est simulé côté calcul : c'est le même chemin de code que
sur un vrai compte, ce qui en fait aussi un test d'intégration qui se regarde.

DEUX RÈGLES, et elles ne sont pas négociables :

1. **Rien n'est écrit hors du dossier temporaire.** Pas de contamination du registre réel
   — une ligne inventée qui se glisserait dans un vrai historique y resterait pour de
   bon, et fausserait des projections auxquelles quelqu'un fait confiance.
2. **C'est annoncé comme faux, en toutes lettres, à chaque affichage.** Un chiffre
   plausible non étiqueté est pire qu'un chiffre absent : c'est exactement la ligne verte
   non observée que tout le dépôt refuse.

Le scénario est choisi pour montrer les deux moitiés de la règle du dépôt :

- la **mensualisation des impôts** et le loyer creusent le solde sous le plancher avant
  la fin du mois — ce que la banque ne dira que le jour même, trop tard ;
- la **taxe foncière** n'est passée que deux fois en 400 jours. Deux passages sont une
  coïncidence : elle sort en `confidence: "faible"` et n'entre PAS dans la trajectoire,
  qui l'annonce donc en toutes lettres comme optimiste.
"""

from __future__ import annotations

from datetime import date, timedelta

from . import ledger

COMPTE = "demo-compte-fictif"
SOLDE_DEPART = 1284.55

# Le solde de départ est choisi pour que le loyer laisse encore au-dessus du plancher et
# que les impôts fassent passer dessous : le déclencheur affiché est « impôts », qui est
# exactement l'histoire que raconte le README. Un solde plus confortable donnerait une
# démonstration où il ne se passe rien.

# 400 jours : au-delà des 380 qu'il faut pour qu'une échéance annuelle ait pu passer
# deux fois. C'est ce qui permet de montrer le cas « vue deux fois, donc pas crue ».
PROFONDEUR_JOURS = 400


def _mensuel(depuis: date, jusqu_a: date, jour_du_mois: int, montant: float,
             libelle: str, variation: float = 0.0) -> list[dict]:
    """Une échéance qui tombe le même jour chaque mois, décalée hors week-end.

    Le décalage n'est pas un détail cosmétique : un prélèvement au 5 qui tombe un dimanche
    part le lundi 6, et c'est précisément le bruit que la détection doit encaisser sans
    perdre la cadence. Une démonstration trop propre ne prouverait rien.
    """
    out, n = [], 0
    annee, mois = depuis.year, depuis.month
    while True:
        try:
            jour = date(annee, mois, jour_du_mois)
        except ValueError:            # 31 février et compagnie
            jour = date(annee, mois, 28)
        if jour > jusqu_a:
            break
        if jour >= depuis:
            if jour.weekday() >= 5:   # samedi ou dimanche → lundi
                jour += timedelta(days=7 - jour.weekday())
            # Variation déterministe : un tiers de reproductibilité vaut mieux qu'un
            # hasard qui rendrait la démonstration différente à chaque exécution.
            delta = variation * (((n * 7) % 5) - 2) / 2
            out.append({"date": jour, "montant": round(montant + delta, 2),
                        "libelle": libelle})
            n += 1
        mois += 1
        if mois > 12:
            mois, annee = 1, annee + 1
    return out


def transactions(aujourdhui: date | None = None) -> list[dict]:
    """400 jours d'historique inventé, au format que rend `recurring.normaliser()`.

    Les jours d'échéance sont **calés sur la date d'exécution**, et c'est assumé : une
    démonstration doit montrer le cas qui vaut la peine, quel que soit le jour où on la
    lance. Avec des dates fixes, la moitié du mois donnerait « ✔ tout va bien » — vrai,
    mais sans intérêt, puisque c'est précisément ce que la banque sait déjà dire.

    Le scénario est donc toujours le même : le loyer passe dans trois jours, les impôts
    dans cinq, et le salaire n'arrive que dans neuf. Le creux tombe entre les deux.
    """
    fin = aujourdhui or date.today()
    debut = fin - timedelta(days=PROFONDEUR_JOURS)

    def jour_dans(n: int) -> int:
        return (fin + timedelta(days=n)).day

    lignes: list[dict] = []
    lignes += _mensuel(debut, fin, jour_dans(9), 2450.00, "VIR SEPA SALAIRE EMPLOYEUR SA")
    lignes += _mensuel(debut, fin, jour_dans(3), -890.00, "PRLV SEPA LOYER SCI LES TILLEULS")
    lignes += _mensuel(debut, fin, jour_dans(5), -412.00, "PRLV SEPA DGFIP IMPOT REVENU")
    lignes += _mensuel(debut, fin, jour_dans(12), -132.40, "PRLV SEPA EDF FACTURE",
                       variation=38.0)
    lignes += _mensuel(debut, fin, jour_dans(18), -68.30, "PRLV SEPA MAIF ASSURANCE HABITATION")
    lignes += _mensuel(debut, fin, jour_dans(21), -39.99, "PRLV SEPA OPERATEUR FIBRE")

    # L'annuelle : DEUX passages, à un an d'écart, tous deux dans la fenêtre. Deux
    # passages sont une coïncidence aux yeux du détecteur — elle sort donc en
    # `confidence: "faible"` et n'entre PAS dans la trajectoire. C'est le cas que cette
    # démonstration existe pour montrer : la projection est optimiste, et le dit.
    for recul in (372, 7):
        lignes.append({"date": fin - timedelta(days=recul), "montant": -1247.00,
                       "libelle": "PRLV SEPA DGFIP TAXE FONCIERE"})

    # Du bruit : des courses irrégulières, chez des enseignes qui alternent. Sans lui,
    # la détection travaillerait sur un historique trop propre pour être honnête.
    enseignes = ("CB SUPERMARCHE", "CB BOULANGERIE", "CB STATION SERVICE", "CB PHARMACIE")
    jour = debut
    i = 0
    while jour <= fin:
        jour += timedelta(days=3 + (i % 4))
        if jour > fin:
            break
        lignes.append({"date": jour,
                       "montant": -round(18.0 + ((i * 13) % 62), 2),
                       "libelle": enseignes[i % len(enseignes)]})
        i += 1
    return lignes


def monter(store, aujourdhui: date | None = None) -> str:
    """Remplit un magasin JETABLE et rend l'identifiant du compte fictif.

    Le magasin est passé par l'appelant, et c'est volontaire : personne ne peut appeler
    cette fonction « par erreur » sur le vrai magasin sans l'avoir écrit noir sur blanc.
    """
    fin = aujourdhui or date.today()
    store.save_accounts([{
        "id": COMPTE,
        "iban": "FR7630001007941234567890185",   # IBAN d'exemple de la Banque de France
        "nom": "Compte courant (FICTIF)",
        "devise": "EUR",
        "institution_id": "demo",
        "institution_name": "Banque de Démonstration",
        "linked_at": 0,
        # 74 jours : assez pour ne pas déclencher l'alerte de renouvellement à J-14, et
        # assez court pour que le compte à rebours se voie dans `doctor`.
        "consent_expires_at": __import__("time").time() + 74 * 86400,
    }])
    store.put(f"balances-{COMPTE}", {"balances": [{
        "balanceType": "interimAvailable",
        "balanceAmount": {"amount": f"{SOLDE_DEPART:.2f}", "currency": "EUR"},
        "referenceDate": fin.isoformat(),
    }]})
    registre, _ = ledger.fusionner([], transactions(fin))
    ledger.enregistrer(store.ledger_path(COMPTE), registre)
    return COMPTE
