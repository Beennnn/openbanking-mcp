"""Client GoCardless Bank Account Data (ex-Nordigen) — DSP2, LECTURE SEULE.

Pourquoi celui-là : c'est un agrément AIS (agrégation d'informations sur les comptes),
pas PIS (initiation de paiement). Ce n'est pas une politesse de notre part, c'est
structurel — le jeton délivré ici ne PEUT pas déclencher un virement, la banque ne lui
ouvre pas cette porte. Le pire scénario d'une fuite est donc la lecture de l'historique,
jamais un mouvement d'argent. C'est la seule raison pour laquelle ce dossier a le droit
d'exister.

Trois durées à ne jamais confondre, elles expirent toutes les trois et pas ensemble :
  - le jeton d'accès       : 24 h   (renouvelé tout seul par le jeton de rafraîchissement)
  - le jeton de rafraîchissement : 30 j (au-delà, il faut re-signer avec secret_id/key)
  - le CONSENTEMENT bancaire : 90 j max imposé par la DSP2 — celui-là exige que
    l'utilisateur retourne sur le site de sa banque. Rien ne peut le renouveler à sa place.

Le troisième est le piège : les deux premiers se réparent en silence, le troisième non.
Un agrégateur qui se contente de dire « erreur » le 91e jour fait exactement ce que
CLAUDE.md interdit — il laisse croire que le vert d'hier vaut encore aujourd'hui. D'où
`Api.consent_days_left()` et le fait que TOUTE réponse d'ici est datée.

Quotas : GoCardless compte 4 appels par jour et par compte pour balances/, details/ et
transactions/ SÉPARÉMENT. Quatre. Ce n'est pas une limite qu'on frôle, c'est une limite
qu'on atteint en une matinée de mise au point. Les en-têtes de quota sont donc relus à
chaque réponse et rangés dans le magasin (store.py), et le cache n'est pas un confort :
sans lui le brief du matin échoue un jour sur deux.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .erreurs import ApiError, RateLimited

# Réexportés : `from .gocardless import ApiError` reste valide pour qui importe d'ici.
__all__ = ["Api", "ApiError", "RateLimited", "MAX_CONSENT_DAYS", "SCOPE_LECTURE_SEULE"]

BASE = "https://bankaccountdata.gocardless.com/api/v2"

# La DSP2 plafonne le consentement à 90 jours. On demande le maximum : chaque jour non
# demandé est un aller-retour de plus vers le site de la banque, et c'est la seule étape
# de tout ce dossier qui ne peut pas être automatisée.
MAX_CONSENT_DAYS = 90

# Le scope, écrit en toutes lettres pour qu'une relecture puisse le vérifier d'un coup
# d'œil. Il n'existe pas de valeur ici qui autoriserait un paiement — mais un lecteur
# pressé ne le sait pas, alors la liste est explicite et fermée.
SCOPE_LECTURE_SEULE = ["balances", "details", "transactions"]


class Api:
    """Appels HTTP + cycle de vie des jetons. Ne décide rien, ne cache rien.

    Le magasin lui est passé pour qu'il y range les jetons et les quotas ; toute la
    politique (que garder, combien de temps, quoi servir quand ça échoue) vit dans
    store.py et dans les appelants. Ici, seulement le protocole.
    """

    def __init__(self, store, timeout: int = 20):
        self.store = store
        self.timeout = timeout

    # ------------------------------------------------------------------ jetons

    def _access_token(self) -> str:
        tok = self.store.tokens()
        now = time.time()

        # 60 s de marge : un jeton qui expire pendant le vol du paquet donne un 401
        # incompréhensible à l'autre bout.
        if tok.get("access") and tok.get("access_expires_at", 0) > now + 60:
            return tok["access"]

        if tok.get("refresh") and tok.get("refresh_expires_at", 0) > now + 60:
            try:
                fresh = self._post("/token/refresh/", {"refresh": tok["refresh"]}, auth=False)
                tok["access"] = fresh["access"]
                tok["access_expires_at"] = now + int(fresh.get("access_expires", 86400))
                self.store.save_tokens(tok)
                return tok["access"]
            except ApiError:
                # Le rafraîchissement peut être refusé avant sa date (révocation côté
                # GoCardless). On retombe sur les secrets plutôt que d'échouer : c'est
                # récupérable sans intervention humaine, autant le faire.
                pass

        secret_id, secret_key = self.store.secrets()
        if not secret_id or not secret_key:
            raise ApiError(
                "Aucun secret GoCardless. Range-les dans le trousseau : "
                "finance/bankread secrets --set"
            )
        fresh = self._post(
            "/token/new/", {"secret_id": secret_id, "secret_key": secret_key}, auth=False
        )
        tok = {
            "access": fresh["access"],
            "access_expires_at": now + int(fresh.get("access_expires", 86400)),
            "refresh": fresh.get("refresh", ""),
            "refresh_expires_at": now + int(fresh.get("refresh_expires", 2592000)),
        }
        self.store.save_tokens(tok)
        return tok["access"]

    # -------------------------------------------------------------------- HTTP

    def _request(self, method: str, path: str, body: dict | None = None,
                 auth: bool = True) -> Any:
        url = f"{BASE}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if auth:
            headers["Authorization"] = f"Bearer {self._access_token()}"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                self._record_quota(path, resp.headers)
                raw = resp.read().decode() or "{}"
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {"raw": raw}
            self._record_quota(path, e.headers)
            if e.code == 429:
                raise RateLimited(
                    _human(payload) or "Quota GoCardless épuisé pour ce compte.",
                    reset_seconds=_int_or_none(
                        e.headers.get("HTTP_X_RATELIMIT_ACCOUNT_SUCCESS_RESET")
                        or e.headers.get("HTTP_X_RATELIMIT_RESET")
                    ),
                ) from e
            raise ApiError(_human(payload) or f"HTTP {e.code} sur {path}",
                           status=e.code, payload=payload) from e
        except urllib.error.URLError as e:
            raise ApiError(f"Réseau injoignable ({e.reason}) sur {path}") from e

    def _get(self, path: str, auth: bool = True) -> Any:
        return self._request("GET", path, None, auth)

    def _post(self, path: str, body: dict, auth: bool = True) -> Any:
        return self._request("POST", path, body, auth)

    def _record_quota(self, path: str, headers) -> None:
        """Range ce que GoCardless dit de nos quotas. Best-effort, jamais bloquant.

        Les en-têtes sont préfixés `HTTP_` — oui, dans la réponse HTTP elle-même ; c'est
        une bizarrerie de leur passerelle, pas une faute de frappe ici. On lit les deux
        écritures pour ne pas dépendre de leur humeur.
        """
        def h(*names):
            for n in names:
                v = headers.get(n)
                if v is not None:
                    return _int_or_none(v)
            return None

        remaining = h("HTTP_X_RATELIMIT_ACCOUNT_SUCCESS_REMAINING",
                      "X-RateLimit-Account-Success-Remaining")
        limit = h("HTTP_X_RATELIMIT_ACCOUNT_SUCCESS_LIMIT",
                  "X-RateLimit-Account-Success-Limit")
        reset = h("HTTP_X_RATELIMIT_ACCOUNT_SUCCESS_RESET",
                  "X-RateLimit-Account-Success-Reset")
        if remaining is None and limit is None:
            return
        try:
            self.store.save_quota(path, {"remaining": remaining, "limit": limit,
                                         "reset_seconds": reset, "at": time.time()})
        except Exception:
            pass  # un quota mal rangé ne doit jamais faire échouer une lecture réussie

    # ------------------------------------------------------------- ressources

    def institutions(self, country: str = "fr") -> list[dict]:
        return self._get(f"/institutions/?country={country}")

    def create_agreement(self, institution_id: str, historical_days: int) -> dict:
        return self._post("/agreements/enduser/", {
            "institution_id": institution_id,
            "max_historical_days": historical_days,
            "access_valid_for_days": MAX_CONSENT_DAYS,
            "access_scope": SCOPE_LECTURE_SEULE,
        })

    def create_requisition(self, institution_id: str, agreement_id: str,
                           redirect: str, reference: str) -> dict:
        return self._post("/requisitions/", {
            "redirect": redirect,
            "institution_id": institution_id,
            "agreement": agreement_id,
            "reference": reference,
            "user_language": "FR",
        })

    def requisition(self, requisition_id: str) -> dict:
        return self._get(f"/requisitions/{requisition_id}/")

    def account(self, account_id: str) -> dict:
        return self._get(f"/accounts/{account_id}/")

    def balances(self, account_id: str) -> dict:
        return self._get(f"/accounts/{account_id}/balances/")

    def transactions(self, account_id: str, date_from: str | None = None,
                     date_to: str | None = None) -> dict:
        q = []
        if date_from:
            q.append(f"date_from={date_from}")
        if date_to:
            q.append(f"date_to={date_to}")
        suffix = ("?" + "&".join(q)) if q else ""
        return self._get(f"/accounts/{account_id}/transactions/{suffix}")


def _int_or_none(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _human(payload: Any) -> str:
    """Sortir une phrase lisible du corps d'erreur GoCardless, qui a trois formes."""
    if not isinstance(payload, dict):
        return ""
    for key in ("detail", "summary", "message"):
        if isinstance(payload.get(key), str) and payload[key].strip():
            return payload[key].strip()
    # Erreurs de validation : {"institution_id": {"summary": "...", "detail": "..."}}
    for value in payload.values():
        if isinstance(value, dict):
            got = _human(value)
            if got:
                return got
        if isinstance(value, list) and value and isinstance(value[0], str):
            return value[0]
    return ""
