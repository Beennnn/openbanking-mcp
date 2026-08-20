"""La couche que tout le monde appelle : CLI et serveur MCP passent par ici.

Elle porte la seule politique qui compte — **quand se taire**. Le client du fournisseur
sait parler à la banque, store.py sait ranger, recurring.py sait compter ; aucun des trois
ne décide ce qu'on affiche quand la banque ne répond pas. C'est ici.

Et aucun des trois n'est nommé ici : `provider.charger()` rend un objet qui tient le
contrat, on ne sait pas lequel. C'est ce qui a permis de changer de fournisseur sans
toucher une ligne de ce fichier — sauf celle-ci.

La règle est celle de CLAUDE.md, mot pour mot : aucune ligne verte qui n'ait été
observée. Concrètement, toute réponse d'ici porte un champ `etat` qui vaut

    "observe"  — lu à l'instant, ou assez récemment pour être encore vrai
    "ancien"   — servi depuis le cache, avec son âge en clair ; à lire, pas à croire
    "inconnu"  — on n'a rien, ou plus rien d'assez frais pour prétendre quoi que ce soit

et jamais un solde nu. Un appelant qui affiche le montant sans regarder `etat` produit
exactement le tableau que ce dépôt refuse : du vert dont on doit se demander s'il est
vrai. Le brief du matin, lui, dit « solde inconnu depuis hier » — c'est moins agréable et
c'est le but.

Le quota de quatre appels par jour et par compte (gocardless.py) rend ce choix obligatoire
autant que vertueux : on ne PEUT pas rafraîchir à chaque question.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

from . import ledger, recurring
from .erreurs import ApiError, RateLimited
from .provider import Fournisseur
from .store import FRESH_SECONDS, Store

# Historique demandé pour la détection des échéances. Treize mois : douze pour voir
# passer une échéance annuelle (taxe foncière, assurance) DEUX fois, plus un mois de
# marge. En dessous, l'annuel n'existe pas ; au-dessus, beaucoup de banques refusent.
HISTORIQUE_JOURS = 400

# Les banques ne rendent pas toutes 400 jours. On demande, et on se contente de ce qui
# vient — mais on le DIT, parce qu'une échéance annuelle absente d'un historique de
# 90 jours ressemble en tout point à une échéance qui n'existe pas.
HISTORIQUE_MINI_POUR_ANNUEL = 380


def comptes(store: Store) -> list[dict]:
    """Les comptes liés, avec l'état de leur consentement DSP2.

    Le consentement est la seule chose ici qu'aucun automatisme ne peut renouveler :
    au bout de 90 jours il faut retourner sur le site de sa banque. On compte
    donc les jours restants à voix haute, et on commence à le dire à J-14 — pas à J-1,
    où il serait déjà trop tard pour un week-end.
    """
    out = []
    for acc in store.accounts():
        jours = store.consent_days_left(acc)
        out.append({
            "id": acc.get("id", ""),
            "nom": acc.get("nom") or acc.get("name") or acc.get("ownerName") or "",
            "iban_fin": (acc.get("iban") or "")[-4:],
            "banque": acc.get("institution_name", acc.get("institution_id", "")),
            "consentement_jours_restants": None if jours is None else round(jours),
            "consentement_a_renouveler": bool(jours is not None and jours <= 14),
        })
    return out


def _compte(store: Store, account_id: str | None) -> dict | None:
    accs = store.accounts()
    if not accs:
        return None
    if account_id:
        return next((a for a in accs if a.get("id") == account_id), None)
    return accs[0]


def soldes(api: Fournisseur | None, store: Store, account_id: str | None = None,
           rafraichir: bool = False) -> list[dict]:
    """Le solde de chaque compte lié, avec son état de fraîcheur.

    `api=None` interdit tout appel réseau : le brief s'en sert pour une seconde lecture
    dans la même matinée sans entamer le quota.
    """
    cibles = store.accounts()
    if account_id:
        cibles = [a for a in cibles if a.get("id") == account_id]

    out = []
    for acc in cibles:
        aid = acc.get("id", "")
        cle = f"balances-{aid}"
        valeur, age, perime = store.cached(cle)

        doit_appeler = rafraichir or not store.fresh(cle)
        souci = ""
        if doit_appeler and api is not None:
            try:
                valeur = api.balances(aid)
                store.put(cle, valeur)
                age, perime = 0.0, False
            except RateLimited as e:
                souci = f"quota épuisé pour ce compte{_reset(e)}"
            except ApiError as e:
                souci = str(e)
        elif doit_appeler and api is None and valeur is None:
            # Un cache de huit heures suffit en mode hors ligne : ne signaler l'absence
            # de réseau que quand elle laisse VRAIMENT sans réponse, sinon le brief
            # remonte un souci tous les matins pour un chiffre qui va très bien.
            souci = "lecture hors ligne et rien en cache"

        montant, devise, date_ref = _meilleur_solde(valeur)
        etat = _etat(valeur is not None, age, perime)
        out.append({
            "compte": aid,
            "nom": acc.get("nom") or acc.get("name") or "",
            "banque": acc.get("institution_name", ""),
            "etat": etat,
            "solde": montant if etat != "inconnu" else None,
            "devise": devise,
            "date_banque": date_ref,
            "age_secondes": None if age is None else round(age),
            "age_lisible": _age_lisible(age),
            "souci": souci,
            "consentement_jours_restants": _jours(store, acc),
        })
    return out


def transactions(api: Fournisseur | None, store: Store, account_id: str | None = None,
                 jours: int = HISTORIQUE_JOURS, rafraichir: bool = False) -> dict:
    """L'historique d'un compte : on lit la fenêtre de la banque, on la FOND dans le
    registre, et on sert le registre.

    Le détour par ledger.py n'est pas une optimisation, c'est ce qui rend l'outil
    possible sur BoursoBank : la banque ne rend que ~90 jours, donc sans accumulation
    une échéance annuelle serait invisible à vie. Avec, elle apparaît au deuxième
    passage — dans un an, mais elle apparaît.

    Une seule fenêtre est demandée, la plus large : réclamer 30 jours puis 400 le même
    matin coûterait deux appels sur les quatre du quota quotidien. On tire large une
    fois, on découpe en mémoire.
    """
    acc = _compte(store, account_id)
    if not acc:
        return {"etat": "inconnu", "souci": "aucun compte lié", "transactions": [],
                "registre_jours": 0, "annuel_detectable": False}

    aid = acc.get("id", "")
    cle = f"tx-{aid}"
    valeur, age, perime = store.cached(cle)
    souci = ""

    if (rafraichir or not store.fresh(cle)) and api is not None:
        depuis = (date.today() - timedelta(days=HISTORIQUE_JOURS)).isoformat()
        try:
            valeur = api.transactions(aid, date_from=depuis)
            store.put(cle, valeur)
            age, perime = 0.0, False
        except RateLimited as e:
            souci = f"quota épuisé pour ce compte{_reset(e)}"
        except ApiError as e:
            souci = str(e)

    chemin = store.ledger_path(aid)
    registre = ledger.charger(chemin)
    brutes = ((valeur or {}).get("transactions") or {}).get("booked") or []
    lues = recurring.normaliser(brutes)

    nouvelles = 0
    if lues:
        registre, nouvelles = ledger.fusionner(registre, lues)
        if nouvelles or not chemin.exists():
            ledger.enregistrer(chemin, registre)

    limite = (date.today() - timedelta(days=jours)).isoformat()
    lignes = [l for l in registre if l["date"] >= limite]
    lignes.sort(key=lambda l: l["date"], reverse=True)

    profondeur = ledger.profondeur_jours(registre)
    return {
        "compte": aid,
        "nom": acc.get("nom") or acc.get("name") or "",
        "etat": _etat(bool(registre), age, perime),
        "age_lisible": _age_lisible(age),
        "souci": souci,
        "fenetre_jours": jours,
        "registre_jours": profondeur,
        "nouvelles_lignes": nouvelles,
        "annuel_detectable": profondeur >= HISTORIQUE_MINI_POUR_ANNUEL,
        "transactions": lignes,
    }


def echeances(api: Fournisseur | None, store: Store, account_id: str | None = None,
              rafraichir: bool = False) -> dict:
    """Les prélèvements et rentrées qui reviennent, détectés sur TOUT le registre.

    Contrairement aux soldes, une échéance ne se périme pas en six heures : elle décrit
    le passé, qui ne bouge plus. Une lecture ratée ce matin ne rend donc pas les
    échéances inconnues — elle rend seulement la date du prochain passage un peu moins
    sûre si un prélèvement est tombé entre-temps. C'est ce que dit `age_lisible`, pendant
    que `echeances` continue de répondre.
    """
    hist = transactions(api, store, account_id, jours=HISTORIQUE_JOURS,
                        rafraichir=rafraichir)
    lignes = [
        {"date": date.fromisoformat(t["date"]), "montant": t["montant"],
         "libelle": t["libelle"]}
        for t in hist["transactions"]
    ]
    trouvees = recurring.detecter(lignes)
    profondeur = hist.get("registre_jours", 0)

    return {
        "compte": hist.get("compte", ""),
        "nom": hist.get("nom", ""),
        "etat": hist["etat"],
        "age_lisible": hist.get("age_lisible", ""),
        "souci": hist.get("souci", ""),
        "historique_jours": profondeur,
        "annuel_detectable": profondeur >= HISTORIQUE_MINI_POUR_ANNUEL,
        "jours_avant_annuel_detectable": max(0, HISTORIQUE_MINI_POUR_ANNUEL - profondeur),
        "echeances": [e.as_dict() for e in trouvees],
    }


def projection(api: Fournisseur | None, store: Store, account_id: str | None = None,
               jours: int = 45, plancher: float = 0.0,
               rafraichir: bool = False) -> dict:
    """Solde d'aujourd'hui moins ce qui va tomber. Le croisement, enfin.

    Refuse de projeter sur un solde qu'on n'a pas observé : partir d'un chiffre d'avant-
    hier pour annoncer une date de découverte au jour près serait une précision inventée.
    """
    sol = soldes(api, store, account_id, rafraichir=rafraichir)
    if not sol:
        return {"etat": "inconnu", "souci": "aucun compte lié"}
    s = sol[0]
    if s["etat"] == "inconnu" or s["solde"] is None:
        return {"etat": "inconnu", "compte": s["compte"], "nom": s["nom"],
                "souci": s["souci"] or "solde jamais observé — rien à projeter"}

    ech = echeances(api, store, s["compte"], rafraichir=False)
    objets = [
        recurring.Echeance(
            libelle=e["libelle"], montant=e["montant"], cadence=e["cadence"],
            jours_entre=_jours_cadence(e["cadence"]), occurrences=e["occurrences"],
            derniere=date.fromisoformat(e["derniere"]),
            prochaine=date.fromisoformat(e["prochaine"]),
            confidence=e["confidence"], famille=e["famille"],
            incertitude_jours=e["incertitude_jours"],
        )
        for e in ech["echeances"]
    ]
    resultat = recurring.projeter(s["solde"], objets, jours=jours, plancher=plancher)
    resultat.update({
        "compte": s["compte"],
        "nom": s["nom"],
        "banque": s["banque"],
        "etat": s["etat"],
        "solde_observe_il_y_a": s["age_lisible"],
        "historique_jours": ech["historique_jours"],
        "annuel_detectable": ech["annuel_detectable"],
        "souci": s["souci"] or ech["souci"],
    })
    return resultat


def sante(store: Store) -> dict:
    """Le check du dossier : ce qui marche, ce qui va casser, et quand.

    Écrit pour être lu d'un coup d'œil avant de faire confiance au reste. Ne fait aucun
    appel réseau : un diagnostic qui consomme du quota est un diagnostic qu'on n'ose pas
    lancer, donc qu'on ne lance pas, donc qui ne sert à rien.
    """
    accs = store.accounts()
    tok = store.tokens()
    now = time.time()

    problemes = []
    if not store.has_secrets():
        problemes.append("aucun identifiant de fournisseur dans le trousseau (bankread secrets --set)")
    if not accs:
        problemes.append("aucune banque liée (bankread link)")

    for acc in accs:
        jours = store.consent_days_left(acc)
        nom = acc.get("nom") or acc.get("name") or acc.get("id", "")[:8]
        if jours is None:
            problemes.append(f"{nom} : date de consentement inconnue, relier par sécurité")
        elif jours <= 0:
            problemes.append(f"{nom} : CONSENTEMENT EXPIRÉ — bankread link, plus rien ne se lit")
        elif jours <= 14:
            problemes.append(f"{nom} : consentement à renouveler dans {round(jours)} j")

    registres = {}
    for acc in accs:
        aid = acc.get("id", "")
        profondeur = ledger.profondeur_jours(ledger.charger(store.ledger_path(aid)))
        registres[acc.get("nom") or aid[:8]] = {
            "historique_accumule_jours": profondeur,
            "annuel_detectable": profondeur >= HISTORIQUE_MINI_POUR_ANNUEL,
            "jours_restants_avant_annuel": max(0, HISTORIQUE_MINI_POUR_ANNUEL - profondeur),
        }

    return {
        "secrets": store.has_secrets(),
        "comptes_lies": len(accs),
        "registres": registres,
        "jeton_acces_valide": bool(tok.get("access_expires_at", 0) > now),
        "jeton_refresh_expire_dans_jours": (
            round((tok.get("refresh_expires_at", 0) - now) / 86400, 1)
            if tok.get("refresh_expires_at") else None
        ),
        "quotas": store.quotas(),
        "problemes": problemes,
        "verdict": "ok" if not problemes else "a_regarder",
    }


# --------------------------------------------------------------------- outils

def _etat(a_des_donnees: bool, age: float | None, perime: bool) -> str:
    if not a_des_donnees or age is None or perime:
        return "inconnu"
    return "observe" if age < FRESH_SECONDS else "ancien"


def _meilleur_solde(payload) -> tuple[float | None, str, str]:
    """Le solde qui répond à « combien puis-je dépenser ».

    `interimAvailable` d'abord : il tient compte des opérations en cours, donc de la
    carte passée ce matin. `closingBooked` l'ignore et flatte le compte de tout ce qui
    n'est pas encore comptabilisé — c'est le chiffre qui fait croire qu'on a de la marge
    la veille du prélèvement.
    """
    if not isinstance(payload, dict):
        return None, "", ""
    balances = payload.get("balances") or []
    # `closingAvailable` (CLAV en ISO 20022) est ce que rendent beaucoup de banques
    # françaises via Enable Banking : c'est un solde DISPONIBLE, donc il passe devant les
    # soldes comptables, juste derrière `interimAvailable` qui est plus frais encore.
    for prefere in ("interimAvailable", "closingAvailable", "closingBooked", "expected",
                    "forwardAvailable"):
        for b in balances:
            if b.get("balanceType") == prefere:
                montant = b.get("balanceAmount", {})
                try:
                    return (round(float(montant.get("amount", "")), 2),
                            montant.get("currency", "EUR"),
                            b.get("referenceDate", ""))
                except (TypeError, ValueError):
                    continue
    return None, "", ""


def _jours(store: Store, acc: dict) -> int | None:
    j = store.consent_days_left(acc)
    return None if j is None else round(j)


def _jours_cadence(nom: str) -> float:
    return next((c for n, c, _ in recurring.CADENCES if n == nom), 30.0)


def _reset(e: RateLimited) -> str:
    if not e.reset_seconds:
        return ""
    return f" (repasse dans {round(e.reset_seconds / 3600, 1)} h)"


def _age_lisible(age: float | None) -> str:
    if age is None:
        return "jamais lu"
    if age < 90:
        return "à l'instant"
    if age < 5400:
        return f"il y a {round(age / 60)} min"
    if age < 172800:
        return f"il y a {round(age / 3600)} h"
    return f"il y a {round(age / 86400)} j"
