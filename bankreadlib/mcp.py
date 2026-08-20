"""Serveur MCP (stdio, JSON-RPC 2.0) — six outils, tous en lecture.

Écrit à la main plutôt qu'avec le SDK `mcp` : ce dépôt n'a qu'une dépendance tierce
(`mido`) et rien ici ne justifie la deuxième. Le protocole tient en trois méthodes —
`initialize`, `tools/list`, `tools/call` — et la transcription est plus courte que la
notice d'installation du SDK.

UNE SEULE RÈGLE ABSOLUE : stdout appartient au protocole. Un `print` de mise au point
égaré au milieu du flux casse la trame JSON-RPC et le client se déconnecte sans dire
pourquoi. Tout ce qui se raconte part sur stderr, qui finit dans le log launchd.

Les descriptions d'outils ci-dessous parlent du champ `etat` à chaque fois, exprès. Le
modèle qui les lit doit savoir AVANT d'appeler que `solde: null, etat: "inconnu"` est une
réponse normale et pas une panne — sinon il l'interprète comme un compte à zéro, ce qui
serait la pire lecture possible d'un chiffre manquant.
"""

from __future__ import annotations

import json
import sys
import traceback

from . import read
from .provider import charger
from .store import Store

VERSION = "1.0.0"
PROTOCOLE_PAR_DEFAUT = "2025-06-18"

_ETAT = ("Toute réponse porte `etat` : \"observe\" (lu à l'instant), \"ancien\" (servi "
         "du cache, voir `age_lisible`) ou \"inconnu\" (rien de fiable — ne jamais "
         "présenter cela comme un solde nul ou une panne).")

_COMPTE = {"type": "string",
           "description": "Identifiant du compte. Omis : le premier compte lié."}

OUTILS = [
    {
        "name": "banque_sante",
        "description": (
            "État du lien bancaire : secrets présents, comptes liés, jours restants "
            "avant expiration du consentement DSP2, quotas d'appels, et `registres` — "
            "la profondeur d'historique réellement accumulée par compte, qui est "
            "supérieure à ce que la banque rend aujourd'hui. Aucun appel réseau, "
            "donc gratuit en quota. À appeler en premier quand quelque chose paraît "
            "manquer, et une fois par semaine pour voir venir le renouvellement à 90 j."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "banque_comptes",
        "description": "La liste des comptes liés, leur banque et l'état de leur consentement.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "banque_soldes",
        "description": (
            "Le solde disponible de chaque compte (interimAvailable : opérations en "
            "cours déduites). " + _ETAT + " Le quota est de 4 lectures par jour et par "
            "compte : laisser `rafraichir` à faux sauf demande explicite d'un chiffre "
            "à la seconde."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "compte": _COMPTE,
                "rafraichir": {"type": "boolean",
                               "description": "Forcer un appel réseau. Consomme du quota."},
            },
        },
    },
    {
        "name": "banque_echeances",
        "description": (
            "Les opérations qui reviennent (prélèvements ET rentrées), détectées sur "
            "l'historique : cadence, montant médian, date du prochain passage. Chacune "
            "porte `confidence` : \"sure\" (≥3 passages réguliers) ou \"faible\" (2 "
            "passages — une coïncidence possible, à ne pas annoncer comme un fait). "
            "`annuel_detectable` dit si l'historique remonte assez loin pour qu'une "
            "échéance annuelle ait pu être vue passer deux fois ; s'il est faux, "
            "l'absence de taxe foncière ne prouve RIEN et toute projection est "
            "optimiste — le dire, plutôt que conclure que tout va bien. "
            "`jours_avant_annuel_detectable` donne le délai : la banque ne rend que "
            "~90 jours, mais bankread accumule localement, donc ce nombre descend tout "
            "seul à chaque lecture."
        ),
        "inputSchema": {"type": "object", "properties": {"compte": _COMPTE}},
    },
    {
        "name": "banque_projection",
        "description": (
            "LE croisement : solde d'aujourd'hui moins les échéances sûres à venir, "
            "déroulé jour par jour. Rend `franchissement` (quand le solde passe sous le "
            "plancher, et quelle échéance l'y pousse), `point_bas` et le détail des "
            "mouvements. C'est ce qui répond à « est-ce que ça passe avant la fin du "
            "mois », là où une alerte de seuil bancaire n'avertit qu'après coup. "
            "Refuse de projeter sur un solde non observé plutôt que d'inventer une date."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "compte": _COMPTE,
                "jours": {"type": "integer",
                          "description": "Fenêtre de projection en jours (défaut 45)."},
                "plancher": {"type": "number",
                             "description": "Seuil sous lequel alerter, en euros (défaut 0)."},
            },
        },
    },
    {
        "name": "banque_transactions",
        "description": (
            "Les opérations passées d'un compte, les plus récentes d'abord. Sert à "
            "vérifier une échéance annoncée ou à retrouver un achat précis. " + _ETAT
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "compte": _COMPTE,
                "jours": {"type": "integer", "description": "Profondeur en jours (défaut 30)."},
            },
        },
    },
]


def _appeler(nom: str, args: dict) -> dict:
    store = Store()
    # Pas de présence humaine déclarée : un modèle qui appelle un outil n'est pas un
    # utilisateur devant l'écran de sa banque, et le prétendre lèverait un plafond qui
    # existe précisément pour distinguer les deux.
    api = charger(store)
    if nom == "banque_sante":
        return read.sante(store)
    if nom == "banque_comptes":
        return {"comptes": read.comptes(store)}
    if nom == "banque_soldes":
        return {"soldes": read.soldes(api, store, args.get("compte"),
                                      rafraichir=bool(args.get("rafraichir")))}
    if nom == "banque_echeances":
        return read.echeances(api, store, args.get("compte"))
    if nom == "banque_projection":
        return read.projection(api, store, args.get("compte"),
                               jours=int(args.get("jours", 45)),
                               plancher=float(args.get("plancher", 0)))
    if nom == "banque_transactions":
        return read.transactions(api, store, args.get("compte"),
                                 jours=int(args.get("jours", 30)))
    raise ValueError(f"outil inconnu : {nom}")


def _repondre(rid, resultat=None, erreur=None) -> None:
    msg = {"jsonrpc": "2.0", "id": rid}
    if erreur is not None:
        msg["error"] = erreur
    else:
        msg["result"] = resultat
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _traiter(msg: dict) -> None:
    methode = msg.get("method", "")
    rid = msg.get("id")
    params = msg.get("params") or {}

    # Les notifications (pas d'`id`) ne se répondent pas — répondre à
    # `notifications/initialized` fait tomber certains clients en erreur de protocole.
    if rid is None:
        return

    if methode == "initialize":
        _repondre(rid, {
            "protocolVersion": params.get("protocolVersion", PROTOCOLE_PAR_DEFAUT),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "bankread", "version": VERSION},
        })
    elif methode == "ping":
        _repondre(rid, {})
    elif methode == "tools/list":
        _repondre(rid, {"tools": OUTILS})
    elif methode == "tools/call":
        nom = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            resultat = _appeler(nom, args)
            _repondre(rid, {
                "content": [{"type": "text",
                             "text": json.dumps(resultat, ensure_ascii=False, indent=2)}],
                "structuredContent": resultat,
            })
        except Exception as e:
            # Une erreur d'outil se rend DANS le résultat, pas comme erreur JSON-RPC :
            # le modèle doit pouvoir la lire et l'expliquer (« consentement
            # expiré, retourne sur le site de ta banque ») plutôt que de voir la
            # connexion tomber.
            print(traceback.format_exc(), file=sys.stderr)
            _repondre(rid, {
                "content": [{"type": "text", "text": f"{type(e).__name__} : {e}"}],
                "isError": True,
            })
    else:
        _repondre(rid, erreur={"code": -32601, "message": f"méthode inconnue : {methode}"})


def serve() -> int:
    for ligne in sys.stdin:
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            msg = json.loads(ligne)
        except ValueError:
            print(f"ligne illisible ignorée : {ligne[:120]}", file=sys.stderr)
            continue
        try:
            _traiter(msg)
        except Exception:
            print(traceback.format_exc(), file=sys.stderr)
    return 0
