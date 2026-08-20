"""Où vivent les secrets, l'état et le cache. Rien de tout ça n'est versionné.

La règle de partage : ce qui est propre À CETTE MACHINE et à CE consentement bancaire
reste hors de git. Et ici, une partie n'est pas seulement une spécificité — c'est un
secret.

  trousseau macOS            les identifiants du fournisseur — jamais en clair sur le disque
                             (Enable Banking : identifiant d'application + clé privée RSA)
  ~/.config/bankread/        state.json : comptes liés, dates de consentement, jetons
  ~/.local/share/bankread/   le REGISTRE : l'historique accumulé (voir ledger.py)
  ~/.cache/bankread/         les dernières réponses, DATÉES

Le registre est à part, et le rangement dit pourquoi : ~/.cache se vide sans dommage,
~/.local/share non. BoursoBank ne rend que 90 jours d'opérations ; ce que le registre a
mémorisé au-delà, personne ne peut le redonner. Le confondre avec le cache et le purger
coûterait des mois d'accumulation.

Pourquoi le trousseau et pas un fichier à 600. Parce qu'un fichier se retrouve dans un
tar de sauvegarde, dans un `cat` malheureux collé dans une conversation, dans un rsync
vers un NAS. Le trousseau demande une autorisation explicite et ne suit pas les
sauvegardes de fichiers. Les jetons, eux, restent dans state.json : ils expirent en 24 h
et 30 j et se régénèrent depuis les secrets — les perdre ne coûte qu'un aller-retour
réseau, alors que les perdre AILLEURS (dans le trousseau, à chaque écriture) ferait
apparaître une demande d'autorisation macOS au milieu du brief de 7 h 30.

LE CACHE N'EST PAS UN CONFORT. Quatre appels par jour et par compte (voir gocardless.py) : sans
cache, la deuxième question de la journée sur le même compte échoue. Mais un cache qui
sert du vieux en le faisant passer pour du frais est pire que pas de cache — c'est
exactement la ligne verte non observée que CLAUDE.md refuse. D'où `cached()` qui rend
TOUJOURS le triplet (données, âge, périmé) et jamais les données seules.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.path.expanduser("~/.config/bankread"))
CACHE_DIR = Path(os.path.expanduser("~/.cache/bankread"))
DATA_DIR = Path(os.path.expanduser("~/.local/share/bankread"))

# Un service de trousseau par fournisseur : changer de fournisseur ne doit pas écraser
# les identifiants de l'ancien, sinon un retour en arrière coûte une nouvelle inscription.
KEYCHAIN_SERVICES = {
    "gocardless": "bankread-gocardless",
    "enablebanking": "bankread-enablebanking",
}
KEYCHAIN_SERVICE = KEYCHAIN_SERVICES["gocardless"]  # conservé : ancien nom, encore cité

# Ce que chaque fournisseur range sous ces deux clés. Les noms servent aux invites de
# `bankread secrets --set` : demander « secret_key » pour une clé privée RSA de 1 700
# caractères ferait chercher longtemps.
SECRETS_ATTENDUS = {
    "gocardless": ("secret_id", "secret_key"),
    "enablebanking": ("identifiant d'application", "clé privée RSA (chemin du .pem)"),
}

# Combien de temps une lecture reste « fraîche ». Six heures : quatre appels par jour et
# par endpoint, donc un toutes les six heures est le rythme maximal soutenable. Le brief
# du matin en consomme un ; il en reste trois pour les questions de la journée.
FRESH_SECONDS = 6 * 3600

# Au-delà, on ne prétend plus rien. Une journée entière sans réussir à joindre la banque
# n'est pas « le solde est de X », c'est « je ne sais pas depuis hier ».
STALE_SECONDS = 26 * 3600


class Store:
    def __init__(self, config_dir: Path = CONFIG_DIR, cache_dir: Path = CACHE_DIR,
                 data_dir: Path = DATA_DIR):
        self.config_dir = config_dir
        self.cache_dir = cache_dir
        self.data_dir = data_dir
        self.state_path = config_dir / "state.json"

    def ledger_path(self, account_id: str) -> Path:
        """Le registre d'un compte. Durable — jamais rangé avec le cache."""
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in account_id)
        return self.data_dir / "ledger" / f"{safe}.json"

    # ----------------------------------------------------------------- secrets

    def fournisseur(self) -> str:
        """Qui fournit la donnée. Déduit tant que rien n'a été choisi explicitement.

        La déduction évite une commande de configuration de plus : si la clé Enable
        Banking est dans le trousseau, c'est qu'on l'a mise, donc c'est le fournisseur.
        GoCardless reste le repli — il ne sert plus qu'à un compte ouvert avant la
        fermeture des inscriptions.
        """
        choisi = self.state().get("fournisseur")
        if choisi in KEYCHAIN_SERVICES:
            return choisi
        if self.has_secrets("enablebanking"):
            return "enablebanking"
        return "gocardless"

    def save_fournisseur(self, nom: str) -> None:
        if nom not in KEYCHAIN_SERVICES:
            raise ValueError(f"fournisseur inconnu : {nom}")
        st = self.state()
        st["fournisseur"] = nom
        self.save_state(st)

    def secrets(self, fournisseur: str | None = None) -> tuple[str, str]:
        """Les deux identifiants du fournisseur — trousseau d'abord, environnement ensuite.

        Pour GoCardless : (secret_id, secret_key). Pour Enable Banking : (identifiant
        d'application, contenu PEM de la clé privée).

        L'environnement est là pour les essais et pour une machine sans trousseau ; il
        n'est pas le chemin nominal, parce qu'un secret dans l'environnement se lit dans
        `ps e` et fuit dans les journaux d'un shell mal configuré.
        """
        nom = fournisseur or self.fournisseur()
        service = KEYCHAIN_SERVICES.get(nom, KEYCHAIN_SERVICE)
        prefixe = "ENABLEBANKING" if nom == "enablebanking" else "GOCARDLESS"
        a = _keychain_get(service, f"{service}-id") or os.environ.get(f"{prefixe}_SECRET_ID", "")
        b = _keychain_get(service, f"{service}-key") or os.environ.get(f"{prefixe}_SECRET_KEY", "")
        # La clé privée est multiligne : on ne la rogne que des blancs de bordure, sinon
        # le PEM perd son dernier saut de ligne et certains analyseurs s'en offusquent.
        return a.strip(), b.strip()

    def save_secrets(self, premier: str, second: str, fournisseur: str | None = None) -> None:
        nom = fournisseur or self.fournisseur()
        service = KEYCHAIN_SERVICES.get(nom, KEYCHAIN_SERVICE)
        _keychain_set(service, f"{service}-id", premier)
        _keychain_set(service, f"{service}-key", second)

    def has_secrets(self, fournisseur: str | None = None) -> bool:
        a, b = self.secrets(fournisseur)
        return bool(a and b)

    # ------------------------------------------------------------------- état

    def state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def save_state(self, state: dict) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
        # Remplacement atomique : un brief qui lit pendant qu'on écrit doit voir l'ancien
        # état complet, jamais un demi-fichier.
        tmp.replace(self.state_path)
        try:
            self.state_path.chmod(0o600)
        except OSError:
            pass

    def tokens(self) -> dict:
        return self.state().get("tokens", {})

    def save_tokens(self, tokens: dict) -> None:
        st = self.state()
        st["tokens"] = tokens
        self.save_state(st)

    def save_quota(self, path: str, quota: dict) -> None:
        st = self.state()
        st.setdefault("quotas", {})[_endpoint_kind(path)] = quota
        self.save_state(st)

    def quotas(self) -> dict:
        return self.state().get("quotas", {})

    # --------------------------------------------------------------- comptes

    def accounts(self) -> list[dict]:
        """Les comptes liés, tels que `bankread link` les a enregistrés."""
        return self.state().get("accounts", [])

    def save_accounts(self, accounts: list[dict]) -> None:
        st = self.state()
        st["accounts"] = accounts
        self.save_state(st)

    def add_link(self, institution_id: str, institution_name: str,
                 requisition_id: str, accounts: list[dict], consent_days: int) -> None:
        """Enregistre un consentement fraîchement signé et les comptes qu'il ouvre.

        Re-lier une banque déjà liée REMPLACE ses comptes au lieu de les empiler : sinon
        chaque renouvellement trimestriel doublerait la liste, et le brief compterait le
        même solde deux fois.
        """
        st = self.state()
        kept = [a for a in st.get("accounts", []) if a.get("institution_id") != institution_id]
        now = time.time()
        for acc in accounts:
            acc = dict(acc)
            acc["institution_id"] = institution_id
            acc["institution_name"] = institution_name
            acc["requisition_id"] = requisition_id
            acc["linked_at"] = now
            acc["consent_expires_at"] = now + consent_days * 86400
            kept.append(acc)
        st["accounts"] = kept
        self.save_state(st)

    def consent_days_left(self, account: dict) -> float | None:
        exp = account.get("consent_expires_at")
        if not exp:
            return None
        return (float(exp) - time.time()) / 86400

    # ---------------------------------------------------------------- cache

    def _cache_path(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
        return self.cache_dir / f"{safe}.json"

    def put(self, key: str, value: Any) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._cache_path(key)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"at": time.time(), "value": value}, ensure_ascii=False))
        tmp.replace(path)

    def cached(self, key: str) -> tuple[Any, float | None, bool]:
        """(valeur, âge en secondes, périmé). Valeur None = rien en cache.

        Rend toujours les trois. Un appelant qui ne veut que la valeur doit écrire
        `value, age, stale = store.cached(k)` et donc VOIR qu'il ignore l'âge — c'est
        volontaire, on rend l'oubli visible à la relecture.
        """
        try:
            blob = json.loads(self._cache_path(key).read_text())
        except (OSError, ValueError):
            return None, None, True
        age = time.time() - float(blob.get("at", 0))
        return blob.get("value"), age, age > STALE_SECONDS

    def fresh(self, key: str) -> bool:
        _, age, _ = self.cached(key)
        return age is not None and age < FRESH_SECONDS


def _endpoint_kind(path: str) -> str:
    for kind in ("balances", "transactions", "details"):
        if path.rstrip("/").endswith(kind):
            return kind
    return "other"


def _keychain_get(service: str, account: str) -> str:
    try:
        p = subprocess.run(
            ["security", "find-generic-password", "-s", service,
             "-a", account, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""  # pas de macOS, pas de trousseau : l'environnement prendra le relais
    return p.stdout.strip() if p.returncode == 0 else ""


def _keychain_set(service: str, account: str, secret: str) -> None:
    # -U met à jour si l'entrée existe déjà, sinon `security` refuse le doublon.
    subprocess.run(
        ["security", "add-generic-password", "-s", service,
         "-a", account, "-w", secret, "-U"],
        check=True, capture_output=True, text=True, timeout=10,
    )
