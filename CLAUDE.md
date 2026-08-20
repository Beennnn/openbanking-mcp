# openbanking-mcp — lecture seule des comptes, et le brief qui prévient avant

**Le dépôt s'appelle `openbanking-mcp`, la commande s'appelle `bankread`**, et les chemins
de configuration suivent la commande (`~/.config/bankread`, trousseau
`bankread-enablebanking`). Le nom du dépôt dit ce que c'est — c'est comme ça qu'on le
trouve — le nom de la commande dit ce qu'on en fait. Ne pas « harmoniser » : renommer la
commande toucherait le dossier de configuration, le trousseau, l'agent launchd et toute
déclaration MCP existante, pour rien.

Outil personnel, publié tel quel (MIT, dépôt public). Deux choses, et rien d'autre : lire ses comptes
bancaires par la DSP2 (agrément AIS, **jamais** d'initiation de paiement), et en tirer un
brief quotidien qui pose quelques tâches Todoist.

Python 3.11+, **aucune dépendance tierce** — `urllib` pour l'HTTP, JSON-RPC écrit à la
main pour MCP. Ne pas en ajouter : rien ici ne le justifie, et une dépendance de plus,
c'est une mise à jour de plus à surveiller sur un outil qui touche à de l'argent.

## Le fournisseur de données : Enable Banking

`bankreadlib/gocardless.py` visait **GoCardless Bank Account Data** (ex-Nordigen), **fermé
aux nouvelles inscriptions et en cours d'arrêt** (vérifié le 2026-08-20). Il est gardé
pour un compte déjà ouvert, il ne sert à personne d'autre.

Le fournisseur courant est **Enable Banking**, palier *Restricted Production* : vraie
donnée de production, restreinte aux comptes qu'on déclare soi-même dans leur portail,
gratuit, inscription en libre-service par email — ni société ni certificat eIDAS. Le tour
des alternatives et les sources sont dans `docs/fournisseurs.md` : s'y référer plutôt que
d'écrire de mémoire, et le mettre à jour si le paysage bouge.

`bankreadlib/rs256.py` signe les jetons **sans dépendance** : RS256 se ramène à `hashlib`
plus `pow(m, d, n)`. Le test `test_la_signature_est_celle_d_openssl` compare octet pour
octet à `openssl dgst -sha256 -sign` — c'est ce qui autorise à se passer de `PyJWT` et de
`cryptography`. Ce module ne doit jamais servir à VÉRIFIER une signature venue de
l'extérieur : une implémentation maison n'a aucune protection contre la mesure du temps
de calcul.

**La fenêtre d'or d'une heure** est la découverte qui change le code : l'historique
complet (un à trois ans) n'est servi que pendant environ une heure après la signature du
consentement, ensuite la banque retombe à 90 jours glissants. `link` doit enchaîner sur un
rapatriement profond dans la foulée. C'est aussi ce qui rachète le registre : cette heure-là
ne se rejoue que tous les 90 à 180 jours.

**Un seul fichier connaît le fournisseur** — et c'est vrai depuis `provider.py`, pas
avant. `read.py` importait `gocardless` directement : le dépôt entier connaissait donc
son fournisseur pendant que le README promettait le contraire. Le contrat vit maintenant
dans un `Protocol` (`provider.Fournisseur`), `provider.charger()` choisit le client, et
un test vérifie que le contrat tient en deux méthodes. `ledger.py`, `recurring.py`,
`read.py`, `mcp.py`, `brief/` et `launchd/` ne nomment plus aucun fournisseur : le
vérifier d'un `grep` avant de committer.

**Le signe des montants est la traduction qui compte.** Enable Banking livre des montants
toujours positifs et met le sens à côté (`credit_debit_indicator`). Les recopier tels
quels ferait compter chaque prélèvement comme une rentrée, et la projection remonterait à
chaque facture. `test_un_debit_devient_un_montant_negatif` garde ce cas.

## La règle qui gouverne tout le reste

**Aucune ligne verte qui n'ait été observée.** Un solde qu'on n'a pas lu n'est pas zéro,
une échéance vue deux fois n'est pas une échéance, une projection qui ignore les annuelles
est optimiste et doit le dire. Toute réponse de `read.py` porte un champ `etat` —
`observe` / `ancien` (avec son âge) / `inconnu` — et jamais un montant nu.

C'est ce qui rend le brief lisible d'un coup d'œil : s'il faut se demander si le vert est
un vrai vert, il ne sert plus à rien. Appliqué à de l'argent, l'enjeu n'est plus
esthétique.

Trois conséquences que les tests gardent, et qu'il ne faut pas défaire :

- **Les rentrées comptent autant que les sorties.** Projeter les seuls prélèvements donne
  une courbe qui plonge toujours, donc une alarme tous les jours, donc plus personne qui
  lit le brief en semaine deux. `test_le_salaire_qui_arrive_avant_evite_la_fausse_alerte`.
- **Deux passages sont une coïncidence, trois sont un fait.** `confidence: "faible"` sort
  de la projection.
- **`doctor` ne fait aucun appel réseau.** Un diagnostic qui consomme du quota est un
  diagnostic qu'on n'ose pas lancer. Il dit « vide » plutôt qu'un compte à rebours inventé
  quand rien n'a encore été lu.

## `~/.local/share/bankread/ledger/` n'est PAS un cache

BoursoBank ne rend qu'environ **90 jours** d'opérations. Le premier jet mettait en cache
un instantané : chaque lecture remplaçait la précédente, donc tout ce qui dépassait le
trimestre quittait l'API et le cache en même temps. Une échéance **annuelle** (taxe
foncière, prime d'assurance) n'était pas « pas encore détectée » — elle était
**indétectable à vie**, pendant que `project` traçait une courbe confiante qui l'ignorait.

Le registre fond chaque lecture dans un fichier durable et ne perd rien. Au bout d'un an,
bankread connaît 365 jours là où la banque en montre 90.

- Il vit dans `~/.local/share`, **pas** dans `~/.cache` : le rangement est ce qui rend la
  confusion difficile. Le purger avec le cache coûte des mois que la banque ne peut plus
  redonner.
- La déduplication porte sur le **contenu**, pas sur `internalTransactionId` : certaines
  banques ne le fournissent pas, d'autres le renumérotent d'une lecture à l'autre.
  Comptage par jour en `max(connu, vu)`, jamais en somme.
- Contrepartie assumée et à ne pas masquer : c'est un an d'historique bancaire en clair
  sur le disque, en 0600.

## Ce qui ne se fait jamais ici

- **Aucune écriture bancaire.** Le scope du jeton est AIS et la banque n'ouvre pas
  l'initiation de paiement — mais l'intention compte autant que la contrainte technique.
- **Aucun secret sur le disque.** Trousseau macOS, service `bankread-enablebanking` (un
  service par fournisseur). La **clé privée RSA** y va aussi : `secrets --set` lit le
  `.pem`, le range, et rappelle de supprimer l'original. Un fichier finit dans un tar de
  sauvegarde ou un `cat` malheureux.
- **Aucune présence humaine déclarée à tort.** L'en-tête PSU dit à la banque qu'un
  utilisateur est devant l'écran et lève le plafond d'appels. Il n'est envoyé que si la
  commande a un terminal — le brief de 7 h 30 n'en a pas. C'est une déclaration, pas un
  réglage de performance.
- **Aucun scraping du site de la banque.** Ça viole les CGU, ça casse à la première
  refonte, et surtout ça met le mot de passe complet — donc le pouvoir de virer de
  l'argent — dans une boucle automatisée.
- **Le brief ne répond à personne.** `brief/run-brief` passe une liste FERMÉE d'outils à
  `claude --allowedTools`, sans aucun outil d'écriture Gmail. Il lit le courrier.

## Le consentement expire — trois à six mois, pas 90 jours par principe

La DSP2 ne plafonne plus l'accès à 90 jours ; c'est chaque banque qui fixe sa limite, lue
dans `maximum_consent_validity` et plafonnée à 180 jours côté demande. **C'est la durée
ACCORDÉE qui est enregistrée, jamais celle qui a été demandée** — recopier la demande
ferait annoncer un consentement valide deux mois après sa mort, soit une date verte non
observée.

**Personne ne peut le renouveler à la place de l'utilisateur** : il doit retourner sur le
site de sa banque. `bankread doctor` prévient à J-14, pas à J-1 où un week-end suffirait à tout
périmer. Et chaque renouvellement rouvre la fenêtre d'or : `link` en profite tout seul.

## Où vit quoi

```
bankread                 lanceur de 4 lignes — pour que ./bankread marche depuis un clone
bankreadlib/cli.py       le vrai programme (argparse, parcours de liaison, affichages)
bankreadlib/provider.py  le contrat en Protocol + la fabrique : QUI va chercher la donnée
bankreadlib/enablebanking.py, gocardless.py   les deux seuls fichiers qui nomment un fournisseur
bankreadlib/rs256.py     signature JWT sans dépendance
bankreadlib/read.py      la politique : quand se taire
bankreadlib/ledger.py, recurring.py           l'accumulation et la détection
bankreadlib/demo.py      un compte fictif, pour montrer avant de faire signer
bankreadlib/mcp.py       le serveur MCP (JSON-RPC à la main)
```

`pyproject.toml` rend le tout installable (`pipx`, `uvx`) — le point d'entrée pointe sur
le MÊME `main()` que le lanceur, il n'y a pas deux chemins de code à garder d'accord.

## `bankread demo` est aussi un test qui se regarde

Compte inventé dans un dossier temporaire, 400 jours d'historique fabriqué, **vraie**
détection et **vraie** projection dessus. Deux invariants tenus par les tests : rien ne
s'écrit hors du magasin jetable, et le scénario est calé sur la date d'exécution pour
montrer tous les jours le cas qui vaut la peine (le loyer laisse au-dessus, les impôts
font passer dessous). Une démonstration qui dirait « tout va bien » la moitié du mois
serait vraie et inutile — c'est déjà ce que la banque sait faire.

## Tests

```bash
python3 test_bankread.py
```

La CI (GitHub Actions) les rejoue sur ubuntu ET macOS, en 3.11/3.12/3.13, plus un job qui
construit le paquet et lance la commande installée. macOS est la cible principale
(trousseau, launchd) ; Linux gagne sa place en prouvant le repli — sans le binaire
`security`, `store.py` retombe sur les variables d'environnement.

59 tests, stdlib seule, aucun réseau. Ils vérifient surtout **les cas où le code doit se
taire**. Une détection qui se trompe de date ne plante pas : elle annonce les impôts le 12
au lieu du 15, avec le même aplomb.
