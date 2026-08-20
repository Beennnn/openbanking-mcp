# Quel fournisseur de données pour BoursoBank

Recherche du 2026-08-20, en remplacement de GoCardless Bank Account Data (ex-Nordigen),
fermé aux nouvelles inscriptions. La question posée n'est pas « quel est le meilleur
agrégateur » mais celle-ci, beaucoup plus étroite :

> **Un particulier, sans société et sans licence, peut-il lire SES comptes BoursoBank
> depuis un script qui tourne sur son Mac ?**

La plupart des comparatifs répondent à l'autre question — celle d'une fintech qui a un
budget et un service juridique — et sont donc inutiles ici.

## Le mur qui élimine presque tout : le certificat eIDAS

Pour interroger l'API DSP2 d'une banque **en direct**, il faut être un prestataire agréé
(AISP, enregistré à l'ACPR) et présenter un **certificat eIDAS QWAC** — un certificat
d'identité d'entreprise que seuls quelques autorités de certification émettent. Compter
**2 000 à 10 000 € par an**, plus l'agrément lui-même. C'est hors de portée, et c'est
définitif : aucune astuce technique ne contourne ça.

D'où la seule voie praticable : passer par un agrégateur **qui prête sa licence**, et
qui accepte de le faire sans contrat commercial.

## Le tableau

| Fournisseur | Inscription seul ? | Prix pour cet usage | BoursoBank | Verdict |
|---|---|---|---|---|
| **Enable Banking** | **oui**, par simple email | **gratuit** en *Restricted Production* | oui (connecteur Boursorama intégré) | **retenu** |
| GoCardless Bank Account Data | non — fermé aux nouveaux | (était gratuit) | oui | mort |
| Salt Edge | à confirmer (KYB probable) | palier gratuit annoncé à 3 comptes liés | oui | plan B |
| Bridge (Bankin') | non | **à partir de 499 €/mois** | oui | non |
| Powens (ex-Budget Insight) | non | sur devis, orienté entreprise | oui | non |
| Tink (Visa), Yapily, TrueLayer | non | entreprise + eIDAS | oui | non |
| Plaid | non | à l'appel + eIDAS en Europe | partiel | non |
| open-banking.io | oui, annoncé | forfait | annoncé | trop jeune (voir plus bas) |
| Export CSV/OFX manuel | — | gratuit | oui | **filet de sécurité** |
| Scraping (woob/boobank) | — | gratuit | oui, tant que ça tient | **interdit ici** |

## Pourquoi Enable Banking

Le palier **Restricted Production** existe pour valider une intégration avant de signer :
l'application est activée en production — donc **vraie donnée de vraie banque** — mais
elle ne peut lire que les comptes **qu'on a soi-même déclarés** dans le portail. Tout
autre compte est retiré de la réponse par leur backend. Une limite qui, pour l'usage
d'ici, n'en est pas une : on ne veut lire que ses propres comptes.

Conséquence administrative : dans ce mode, **le KYB n'est pas exigé** (pas de vérification
d'entreprise, pas de liens CGU/politique de confidentialité à fournir). L'inscription se
fait par email au *Control Panel*, et le compte est créé automatiquement.

⚠️ Un article de blog très référencé (dev.to, août 2026) affirme qu'Enable Banking exige
un certificat eIDAS. **C'est faux pour ce mode-là**, et l'article pousse par ailleurs son
propre produit — leur FAQ officielle fait foi.

### Ce que ça implique techniquement

**Écrit et testé le 2026-08-20** — `bankreadlib/enablebanking.py` (client + traduction),
`bankreadlib/rs256.py` (signature sans dépendance), `bankreadlib/provider.py` (le choix et
le contrat). Ce qui suit décrit donc du code existant, plus un plan.

- Authentification par **JWT signé RS256** avec une clé privée RSA générée à la création
  de l'application (fichier `.pem` téléchargé une fois, à mettre dans le trousseau).
  Header `kid` = identifiant d'application ; claims `iss: enablebanking.com`,
  `aud: api.enablebanking.com`, `exp` ≤ `iat` + 24 h. Base : `https://api.enablebanking.com`.
- Parcours : `GET /aspsps?country=FR` → `POST /auth` (renvoie l'URL de la banque à ouvrir)
  → `POST /sessions` (échange le code) → `GET /accounts/{uid}/balances` et
  `GET /accounts/{uid}/transactions`.
- Zéro dépendance tierce reste tenable : RS256 = SHA-256 (stdlib) + exponentiation
  modulaire sur la clé privée (`pow`, stdlib). Pas besoin de `cryptography` ni de `PyJWT`.

## Trois découvertes qui changent le code, pas seulement le fournisseur

**1. La fenêtre d'or d'une heure.** *(traité : `link` enchaîne sur le rapatriement)* L'historique complet — un à trois ans selon la banque —
n'est accessible que **pendant environ une heure après la signature du consentement**.
Passé ce délai, la plupart des banques retombent à 90 jours glissants. C'est énorme ici :
`bankread` doit faire un **rapatriement profond immédiatement après `link`**, sans quoi il
repart à trois mois de mémoire à chaque renouvellement. Bien joué au passage, le registre
`ledger.py` : il est ce qui rend cette heure durablement rentable.

**2. Le consentement peut aller jusqu'à 180 jours**, pas 90. *(traité : on demande
180 j, plafonné par `maximum_consent_validity`, et c'est la durée ACCORDÉE qu'on
enregistre.)* La durée est fixée par le
client dans le champ `valid_until` à l'ouverture de la session, plafonnée par la banque.
La valeur réelle de BoursoBank se lira au premier `bankread banks bourso`.

**3. Le plafond de 4 appels par jour ne vise que le rapatriement en arrière-plan.**
*(traité : en-tête `Psu-User-Agent` envoyé seulement si la commande a un terminal.)* En
transmettant les en-têtes PSU (adresse IP et user-agent de l'utilisateur réellement
présent), la limite ne s'applique pas. Le brief de 7 h 30 tourne sans personne devant
l'écran : il reste donc en arrière-plan, plafonné, et le cache garde tout son sens. Mais
une commande lancée à la main peut légitimement passer les en-têtes.

## Le compte Caisse d'Épargne ne viendra PAS avec BoursoBank

BoursoBank agrège déjà le compte Caisse d'Épargne dans son application, et le montre.
Cette agrégation-là ne traverse pas l'API DSP2 : la DSP2 donne accès aux comptes de
paiement **tenus par** l'établissement interrogé. Pour le compte Caisse d'Épargne,
l'établissement teneur est la Caisse d'Épargne — BoursoBank n'en est que lecteur, au même
titre qu'un agrégateur, et ne redistribue pas ce qu'elle lit.

Il faut donc **deux liaisons, deux consentements, deux renouvellements**. C'est prévu :
`bankread link` s'appelle une fois par banque.

## Les deux voies sans API

**L'export CSV/OFX/QIF** depuis l'espace client (Mes services → Documents → Relevés de
compte → période → Exporter) est gratuit, sans consentement à renouveler, et donne
l'historique que l'espace client conserve — souvent plus que les 90 jours de l'API. Le
geste est manuel, mais mensuel, et il fait vivre la seule chose qui compte : la
soustraction. **À garder comme mode dégradé** : le jour où un fournisseur ferme (c'est
déjà arrivé une fois), un importateur de CSV sauve le registre.

**Le scraping** (module `boursorama` de woob/boobank) marche et ne coûte rien. Il est
**exclu ici**, et pas par prudence excessive : il met le mot de passe complet — donc le
pouvoir de virer de l'argent — dans une boucle automatisée, il viole les CGU, et il casse
à la première refonte du site. Le principe du dépôt est que le pire scénario d'une fuite
reste la lecture d'un historique.

## Et open-banking.io

Apparu en 2026, auto-inscription annoncée, forfait, déchiffrement local revendiqué. Trop
jeune pour lui confier un an d'historique bancaire, et rien de vérifiable sur qui détient
la licence. À revoir si Enable Banking se ferme à son tour.

## Sources

- Enable Banking — [FAQ](https://enablebanking.com/docs/faq/) (restricted production, JWT, 180 jours, fenêtre d'historique, limites d'appels)
- Enable Banking — [Quick start](https://enablebanking.com/docs/api/quick-start/) (inscription, clé RSA, endpoints)
- Enable Banking — [Spécificités France](https://enablebanking.com/docs/markets/fr/)
- Enable Banking — [Changelog février 2024](https://enablebanking.com/blog/2024/03/11/changelog-february-2024) (« Boursorama (FR): Added SEPA payment »)
- [Free & Indie Open Banking APIs (2026)](https://www.openbankingtracker.com/guides/free-open-banking-apis)
- [Alternatives à GoCardless/Nordigen — discussion Firefly III](https://github.com/orgs/firefly-iii/discussions/11875)
- [BoursoBank — portail développeurs DSP2](https://developer.boursorama.com/dsp2)
- [Exporter son relevé BoursoBank en CSV/OFX/QIF](https://www.scancompte.com/banques/exporter-releve-boursorama)
