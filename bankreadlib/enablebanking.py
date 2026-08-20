"""Client Enable Banking — DSP2, LECTURE SEULE.

Remplace GoCardless Bank Account Data (ex-Nordigen), fermé aux nouvelles inscriptions et
en cours d'arrêt. Le tour des solutions praticables pour un particulier sans société ni
certificat eIDAS est dans `docs/fournisseurs.md` ; le résumé tient en une ligne : c'est la
seule voie gratuite qui reste, et Boursorama y est intégrée.

Ce module expose EXACTEMENT le contrat que `read.py` attend, et traduit pour cela le
format d'Enable Banking (snake_case, montants non signés) vers celui du Groupe de Berlin
que le reste du dépôt manipule déjà (camelCase, montants signés). C'est tout l'intérêt de
n'avoir qu'un fichier qui connaît le fournisseur : la traduction se paie ici, une fois.

    balances(uid)                       -> {"balances": [...]}
    transactions(uid, date_from, ...)   -> {"transactions": {"booked": [...]}}

Trois choses à ne pas redécouvrir dans la douleur :

1. **Le signe des montants n'existe pas chez Enable Banking.** Le montant est toujours
   positif et c'est `credit_debit_indicator` qui dit le sens. Recopier `amount` tel quel
   ferait compter les prélèvements comme des rentrées : la projection remonterait
   gaiement à chaque facture EDF. C'est la traduction la plus importante du fichier, et
   `test_un_debit_devient_un_montant_negatif` la garde.

2. **L'historique complet ne s'obtient que dans l'heure qui suit la signature.** Passé ce
   délai, la banque retombe à 90 jours glissants. Le parcours `link` doit donc enchaîner
   sur un rapatriement profond immédiat — sinon le registre repart à trois mois de
   mémoire à chaque renouvellement, et une échéance annuelle reste indétectable à vie.

3. **Le plafond de quatre appels par jour ne vise que le rapatriement en arrière-plan.**
   Transmettre un en-tête PSU le lève, mais il ne se transmet QUE si quelqu'un est
   réellement devant l'écran (voir `presence_humaine`). Le brief de 7 h 30 tourne sans
   personne : il reste plafonné, et le cache garde tout son sens.

L'agrément est AIS (agrégation d'informations), jamais PIS (initiation de paiement) :
aucune méthode d'ici ne peut déplacer un euro, et l'API ne l'ouvrirait pas.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from .erreurs import ApiError, RateLimited
from .rs256 import CleInvalide, jeton

__all__ = ["Api", "ApiError", "RateLimited", "BASE", "MAX_CONSENT_DAYS"]

BASE = "https://api.enablebanking.com"

# Le plafond que l'on DEMANDE. La vraie limite est propre à chaque banque et arrive dans
# `maximum_consent_validity` ; on ne la devine pas, on la lit et on prend le minimum.
# 180 jours, pas 90 : la DSP2 ne plafonne plus l'accès à 90 jours depuis la révision des
# normes techniques, et chaque jour non demandé est un aller-retour de plus vers le site
# de la banque — la seule étape de tout ce dossier qu'on ne peut pas automatiser.
MAX_CONSENT_DAYS = 180

# Durée de vie du jeton d'application. Le maximum accepté est 24 h ; une heure suffit
# largement pour un appel et limite la fenêtre d'un jeton qui traînerait en mémoire.
JETON_SECONDES = 3600

# Enable Banking parle ISO 20022, le reste du dépôt parle Groupe de Berlin. Traduction
# fidèle : on ne maquille pas un solde comptable en solde disponible pour arranger
# l'affichage — c'est `read._meilleur_solde()` qui choisit, en connaissance de cause.
TYPES_DE_SOLDE = {
    "ITAV": "interimAvailable",
    "CLAV": "closingAvailable",
    "CLBD": "closingBooked",
    "ITBD": "interimBooked",
    "XPCD": "expected",
    "FWAV": "forwardAvailable",
    "OPAV": "openingAvailable",
    "OPBD": "openingBooked",
    "PRCD": "previouslyClosedBooked",
    "VALU": "valueDate",
    "INFO": "information",
    "OTHR": "other",
}


class Api:
    """Appels HTTP + signature des jetons. Ne décide rien, ne cache rien.

    Le magasin lui est passé pour qu'il y lise l'identifiant d'application et la clé
    privée ; toute la politique (que garder, combien de temps, quoi servir quand ça
    échoue) vit dans store.py et dans les appelants.
    """

    def __init__(self, store, timeout: int = 20, presence_humaine: bool = False):
        self.store = store
        self.timeout = timeout
        # Ne passer à True que si un humain a VRAIMENT lancé la commande. C'est une
        # déclaration faite à la banque, pas un interrupteur de performance : l'affirmer
        # depuis un agent launchd serait un mensonge, et le genre de mensonge qui se
        # retourne le jour où la banque audite les accès d'un agrégateur.
        self.presence_humaine = presence_humaine
        self._jeton = ""
        self._jeton_expire = 0.0

    # ------------------------------------------------------------------ jeton

    def _autorisation(self) -> str:
        """Un JWT signé RS256. Renouvelé en mémoire, jamais écrit sur le disque.

        Contrairement à GoCardless, il n'y a rien à négocier avec le serveur : le jeton
        se fabrique hors-ligne à partir de la clé privée. Pas d'aller-retour, donc pas de
        jeton de rafraîchissement à surveiller — une durée de moins à confondre.
        """
        if self._jeton and self._jeton_expire > time.time() + 60:
            return self._jeton

        app_id, pem = self.store.secrets("enablebanking")
        if not app_id or not pem:
            raise ApiError(
                "Aucune application Enable Banking configurée. Range l'identifiant et la "
                "clé privée dans le trousseau : bankread secrets --set"
            )
        maintenant = int(time.time())
        try:
            self._jeton = jeton(
                {"typ": "JWT", "alg": "RS256", "kid": app_id},
                {
                    "iss": "enablebanking.com",
                    "aud": "api.enablebanking.com",
                    "iat": maintenant,
                    "exp": maintenant + JETON_SECONDES,
                },
                pem,
            )
        except CleInvalide as e:
            raise ApiError(f"Clé privée Enable Banking illisible : {e}") from e
        self._jeton_expire = maintenant + JETON_SECONDES
        return self._jeton

    # -------------------------------------------------------------------- HTTP

    def _request(self, method: str, path: str, body: dict | None = None,
                 psu: bool = False) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._autorisation()}",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        if psu and self.presence_humaine:
            # Un seul en-tête suffit à signaler la présence. On ne transmet PAS
            # d'adresse IP : bankread ne connaît que celle du réseau local, et inventer
            # une IP publique plausible serait déclarer à la banque quelque chose de faux.
            headers["Psu-User-Agent"] = "bankread (CLI, utilisateur présent)"

        req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            brut = e.read().decode(errors="replace")
            try:
                payload = json.loads(brut)
            except ValueError:
                payload = {"raw": brut}
            if e.code == 429:
                raise RateLimited(
                    _lisible(payload) or "Quota épuisé pour ce compte.",
                    reset_seconds=_entier(e.headers.get("Retry-After")),
                ) from e
            raise ApiError(_lisible(payload) or f"HTTP {e.code} sur {path}",
                           status=e.code, payload=payload) from e
        except urllib.error.URLError as e:
            raise ApiError(f"Réseau injoignable ({e.reason}) sur {path}") from e

    def _get(self, path: str, psu: bool = False) -> Any:
        return self._request("GET", path, None, psu)

    def _post(self, path: str, body: dict) -> Any:
        return self._request("POST", path, body)

    # -------------------------------------------------------------- liaison

    def aspsps(self, country: str = "FR") -> list[dict]:
        """Les banques disponibles pour un pays.

        Enable Banking n'a pas d'identifiant technique : une banque se désigne par son
        NOM et son pays, et c'est ce couple qu'il faut ensuite passer à `start_auth`.
        Le nom est donc à recopier exactement — d'où `bankread banks`.
        """
        rep = self._get(f"/aspsps?country={urllib.parse.quote(country)}")
        return rep.get("aspsps", []) if isinstance(rep, dict) else []

    def start_auth(self, nom: str, pays: str, redirect: str, state: str,
                   jours: int = MAX_CONSENT_DAYS, langue: str = "fr") -> dict:
        """Ouvre un parcours de consentement. Rend {"url", "authorization_id"}."""
        fin = datetime.now(timezone.utc) + timedelta(days=jours)
        return self._post("/auth", {
            "access": {"valid_until": fin.replace(microsecond=0).isoformat()},
            "aspsp": {"name": nom, "country": pays},
            "state": state,
            "redirect_url": redirect,
            "psu_type": "personal",
            "language": langue,
        })

    def create_session(self, code: str) -> dict:
        """Échange le code du retour navigateur contre une session et ses comptes."""
        return self._post("/sessions", {"code": code})

    def session(self, session_id: str) -> dict:
        return self._get(f"/sessions/{session_id}")

    # ------------------------------------------------------------- lectures

    def balances(self, account_id: str) -> dict:
        """Les soldes, traduits en format Groupe de Berlin."""
        rep = self._get(f"/accounts/{account_id}/balances", psu=True)
        soldes = rep.get("balances", []) if isinstance(rep, dict) else []
        return {"balances": [_solde(b) for b in soldes]}

    def transactions(self, account_id: str, date_from: str | None = None,
                     date_to: str | None = None, pages_max: int = 20) -> dict:
        """L'historique, traduit et dépaginé.

        Enable Banking pagine par `continuation_key`. `pages_max` est un garde-fou, pas
        une politique : sur un compte très actif, une pagination sans fin transformerait
        un rapatriement en boucle infinie pendant le brief du matin. Vingt pages couvrent
        largement une année ; au-delà, mieux vaut s'arrêter et le dire.
        """
        params = {}
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to

        booked: list[dict] = []
        pending: list[dict] = []
        vues = 0
        while vues < pages_max:
            requete = urllib.parse.urlencode(params)
            chemin = f"/accounts/{account_id}/transactions"
            rep = self._get(f"{chemin}?{requete}" if requete else chemin, psu=True)
            if not isinstance(rep, dict):
                break
            for t in rep.get("transactions", []) or []:
                traduite = _operation(t)
                cible = pending if _en_attente(t) else booked
                cible.append(traduite)
            suite = rep.get("continuation_key")
            vues += 1
            if not suite:
                break
            params["continuation_key"] = suite

        return {"transactions": {"booked": booked, "pending": pending}}


# ------------------------------------------------------------------ traduction

def _solde(b: dict) -> dict:
    montant = b.get("balance_amount") or {}
    brut = str(b.get("balance_type") or "").upper()
    return {
        "balanceType": TYPES_DE_SOLDE.get(brut, brut or "other"),
        "balanceAmount": {
            "amount": str(montant.get("amount", "")),
            "currency": montant.get("currency", "EUR"),
        },
        "referenceDate": str(b.get("reference_date") or "")[:10],
        "name": b.get("name", ""),
    }


def _operation(t: dict) -> dict:
    montant = t.get("transaction_amount") or {}
    libelles = t.get("remittance_information") or []
    if isinstance(libelles, str):
        libelles = [libelles]
    return {
        "internalTransactionId": t.get("entry_reference", ""),
        "bookingDate": str(t.get("booking_date") or t.get("transaction_date") or "")[:10],
        "valueDate": str(t.get("value_date") or "")[:10],
        "transactionAmount": {
            "amount": _signe(montant.get("amount"), t.get("credit_debit_indicator")),
            "currency": montant.get("currency", "EUR"),
        },
        "creditorName": (t.get("creditor") or {}).get("name", ""),
        "debtorName": (t.get("debtor") or {}).get("name", ""),
        "remittanceInformationUnstructured": libelles[0] if libelles else "",
        "remittanceInformationUnstructuredArray": [str(x) for x in libelles],
    }


def _signe(montant: Any, indicateur: Any) -> str:
    """Rendre au montant le signe qu'Enable Banking porte à côté, pas dedans.

    Tout ce qui n'est pas explicitement un CRÉDIT est traité comme une sortie. Le code de
    débit est écrit `DBIT` dans la norme ISO 20022 et `DBDT` à certains endroits de la
    documentation d'Enable Banking : plutôt que de parier sur l'un des deux, on ne
    reconnaît QUE le crédit. Se tromper dans ce sens fait apparaître une dépense là où il
    y avait une rentrée — une projection trop prudente, jamais une fausse marge.
    """
    try:
        valeur = float(str(montant))
    except (TypeError, ValueError):
        return str(montant if montant is not None else "")
    code = str(indicateur or "").upper()
    if not code:
        return str(valeur)  # déjà signé par la banque : on n'y touche pas
    return str(abs(valeur) if code == "CRDT" else -abs(valeur))


def _en_attente(t: dict) -> bool:
    return str(t.get("status") or "").upper() not in ("BOOK", "BOOKED", "")


def _entier(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _lisible(payload: Any) -> str:
    """Sortir une phrase du corps d'erreur, qui n'a pas toujours la même forme."""
    if not isinstance(payload, dict):
        return ""
    for cle in ("message", "detail", "error", "error_description", "raw"):
        valeur = payload.get(cle)
        if isinstance(valeur, str) and valeur.strip():
            return valeur.strip()[:300]
    for valeur in payload.values():
        if isinstance(valeur, dict):
            trouve = _lisible(valeur)
            if trouve:
                return trouve
    return ""
