"""Signer un JWT en RS256, avec la bibliothèque standard et rien d'autre.

Enable Banking authentifie chaque appel par un jeton JWT signé avec une clé privée RSA
(voir `enablebanking.py`). Tout le monde résout ça avec `PyJWT` + `cryptography` : deux
dépendances, dont une qui embarque du code natif à recompiler à chaque montée de Python.

Sur un outil qui touche à de l'argent et qui doit démarrer sans faute à 7 h 30, c'est
cher payé pour ce que RS256 est réellement :

    signature = (empreinte SHA-256, habillée PKCS#1 v1.5) ^ d  mod  n

`hashlib` fournit l'empreinte, `pow(m, d, n)` fait l'exponentiation modulaire — en C,
dans l'interpréteur, depuis toujours. Il ne reste qu'à lire deux entiers dans le fichier
`.pem`, et c'est ce que fait la moitié de ce module.

CE QUE CE MODULE NE FAIT PAS, et ne doit pas faire : vérifier une signature, chiffrer,
déchiffrer, générer une clé. Une implémentation maison de RSA n'a aucune protection
contre les attaques par canal auxiliaire (mesure du temps de calcul). Ici, ça ne coûte
rien : on signe nos propres jetons, sur notre machine, sans adversaire capable de
chronométrer l'opération. Le jour où il faudrait VÉRIFIER une signature venue de
l'extérieur, ce module ne conviendrait plus — et ce serait le moment d'assumer une
dépendance, pas de rallonger ce fichier.
"""

from __future__ import annotations

import base64
import hashlib

# Préfixe DER de l'empreinte SHA-256 (RFC 8017, EMSA-PKCS1-v1_5) : il déclare « ce qui
# suit est un SHA-256 sur 32 octets ». Sans lui, la signature serait syntaxiquement
# valide mais refusée par tout vérificateur conforme.
_PREFIXE_SHA256 = bytes.fromhex("3031300d060960864801650304020105000420")


class CleInvalide(ValueError):
    """Le fichier fourni n'est pas une clé privée RSA lisible."""


def signer(message: bytes, pem: str) -> bytes:
    """Signature RS256 de `message` par la clé privée `pem`."""
    n, d = cle_privee(pem)
    taille = (n.bit_length() + 7) // 8
    empreinte = _PREFIXE_SHA256 + hashlib.sha256(message).digest()

    # Habillage PKCS#1 v1.5 : 0x00 0x01, du 0xFF jusqu'à remplir, 0x00, puis l'empreinte.
    # Le bourrage n'est pas décoratif — c'est lui qui rend la signature non malléable.
    bourrage = taille - len(empreinte) - 3
    if bourrage < 8:
        raise CleInvalide(
            f"clé de {n.bit_length()} bits trop courte pour signer en SHA-256 "
            f"(il en faut au moins 640)"
        )
    habille = b"\x00\x01" + b"\xff" * bourrage + b"\x00" + empreinte

    signature = pow(int.from_bytes(habille, "big"), d, n)
    return signature.to_bytes(taille, "big")


def jeton(entete: dict, charge: dict, pem: str) -> str:
    """Un JWT complet : entête.charge.signature, chacun en base64url sans remplissage."""
    import json

    def part(obj: dict) -> bytes:
        brut = json.dumps(obj, separators=(",", ":"), sort_keys=True).encode()
        return base64.urlsafe_b64encode(brut).rstrip(b"=")

    corps = part(entete) + b"." + part(charge)
    signature = base64.urlsafe_b64encode(signer(corps, pem)).rstrip(b"=")
    return (corps + b"." + signature).decode()


# --------------------------------------------------------------------- la clé

def cle_privee(pem: str) -> tuple[int, int]:
    """(modulus, exposant privé) — les deux seuls entiers dont `signer` a besoin.

    Les deux formats que produisent les outils courants sont acceptés :
      - PKCS#8  « BEGIN PRIVATE KEY »      — ce que télécharge Enable Banking ;
      - PKCS#1  « BEGIN RSA PRIVATE KEY »  — l'ancien format d'OpenSSL.
    Le second est encapsulé dans le premier, alors on déballe et on relit.
    """
    der = _der_du_pem(pem)
    suite = _sequence(der)

    # PKCS#8 : SEQUENCE { version, SEQUENCE algo, OCTET STRING contenant du PKCS#1 }.
    # On le reconnaît à ce deuxième élément qui est lui-même une séquence.
    if len(suite) >= 3 and suite[1][0] == 0x30:
        interieur = next((v for t, v in suite if t == 0x04), None)
        if interieur is None:
            raise CleInvalide("PKCS#8 sans clé à l'intérieur")
        suite = _sequence(interieur)

    entiers = [v for t, v in suite if t == 0x02]
    # PKCS#1 : version, modulus, exposant public, exposant PRIVÉ, puis les facteurs.
    if len(entiers) < 4:
        raise CleInvalide("clé RSA incomplète (il manque le modulus ou l'exposant privé)")
    n = int.from_bytes(entiers[1], "big")
    d = int.from_bytes(entiers[3], "big")
    if n <= 0 or d <= 0:
        raise CleInvalide("modulus ou exposant privé nul")
    return n, d


def _der_du_pem(pem: str) -> bytes:
    lignes = [l.strip() for l in (pem or "").splitlines()]
    corps = [l for l in lignes if l and not l.startswith("-----")]
    if not corps:
        raise CleInvalide("aucun bloc PEM trouvé (attendu « -----BEGIN … -----»)")
    try:
        return base64.b64decode("".join(corps))
    except Exception as e:  # noqa: BLE001 — base64 lève des choses variées
        raise CleInvalide(f"bloc PEM illisible : {e}") from e


def _sequence(der: bytes) -> list[tuple[int, bytes]]:
    """Déplie une SEQUENCE DER en liste de (étiquette, contenu).

    Un analyseur DER complet serait hors sujet : on ne lit qu'un fichier qu'on a
    soi-même téléchargé depuis son portail, pas une entrée hostile. D'où la portée
    étroite — assez pour trouver deux entiers, et rien de plus.
    """
    etiquette, contenu, reste = _tlv(der)
    if etiquette != 0x30:
        raise CleInvalide("le fichier ne commence pas par une SEQUENCE DER")
    if reste:
        raise CleInvalide("octets en trop après la structure DER")
    elements = []
    while contenu:
        t, v, contenu = _tlv(contenu)
        elements.append((t, v))
    return elements


def _tlv(data: bytes) -> tuple[int, bytes, bytes]:
    """Un triplet DER : (étiquette, valeur, ce qui suit)."""
    if len(data) < 2:
        raise CleInvalide("structure DER tronquée")
    etiquette = data[0]
    premier = data[1]
    if premier < 0x80:
        longueur, debut = premier, 2
    else:
        octets = premier & 0x7F
        if octets == 0 or len(data) < 2 + octets:
            raise CleInvalide("longueur DER invalide")
        longueur = int.from_bytes(data[2:2 + octets], "big")
        debut = 2 + octets
    fin = debut + longueur
    if fin > len(data):
        raise CleInvalide("valeur DER plus longue que le fichier")
    return etiquette, data[debut:fin], data[fin:]
