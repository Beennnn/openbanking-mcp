# `bankread` — lire ses comptes, et prévenir AVANT

Lecture seule des comptes bancaires par la DSP2 (agrément AIS), plus un brief quotidien
vers Todoist. Python 3.11+, **aucune dépendance tierce** : `urllib` pour l'HTTP, JSON-RPC
écrit à la main pour le serveur MCP. Tourne sur le Mac, pas dans un nuage.

> ### ⚠ Le fournisseur de données est à choisir — lire avant de commencer
>
> Ce dépôt est né avec un client **GoCardless Bank Account Data** (ex-Nordigen), qui
> était la voie gratuite évidente. Vérification faite le 2026-08-20 : ce service est
> **fermé aux nouvelles inscriptions et en cours d'arrêt**. `bankreadlib/gocardless.py`
> ne sert donc qu'à qui avait déjà un compte.
>
> Le remplaçant gratuit pour un usage personnel est **Enable Banking**, dont le palier
> *Restricted Production* donne de la vraie donnée de production sur les seuls comptes
> qu'on relie soi-même — ce qui est exactement l'usage ici, et sans licence à payer.
> **Le client Enable Banking est écrit** (`bankreadlib/enablebanking.py`) : inscription
> en libre-service par email, authentification par JWT signé RS256 — sans dépendance, la
> bibliothèque standard suffit (`bankreadlib/rs256.py`). Le tour des solutions praticables
> pour BoursoBank et les trois découvertes qui ont changé le code sont dans
> [`docs/fournisseurs.md`](docs/fournisseurs.md).
>
> Il reste une chose que personne ne peut faire à ta place : **créer le compte Enable
> Banking, y déclarer tes comptes bancaires, et signer le consentement.** L'identifiant
> d'application et la clé privée sont personnels — il n'y a rien à mutualiser, et c'est
> par là qu'il faut commencer.
>
> Tout le reste du dépôt est indépendant du fournisseur.

## Le problème qu'il règle, et celui qu'il ne règle pas

BoursoBank envoie déjà « votre solde est bas ». La Caisse d'Épargne envoie déjà « entrée
d'argent ». Amazon envoie « en cours de livraison », Leboncoin « le vendeur a confirmé ».
**L'information arrive déjà — elle arrive juste trop tard et au mauvais endroit.**

- trop tard : une alerte de seuil se déclenche APRÈS le prélèvement. Le 15 au matin, les
  impôts sont passés, le compte est bas, et l'alerte le constate ;
- au mauvais endroit : au milieu de deux cents mails, dont la plupart ne sont pas lus.

Ce dossier ne va donc pas chercher une information qui manque. Il fait la seule chose que
personne ne fait : **la soustraction**. Le solde d'aujourd'hui moins ce qui va tomber
d'ici la fin du mois. Ni la banque ni les impôts ne peuvent la faire — aucun des deux ne
voit l'autre.

Ce qu'il ne règle pas : rien ici ne remplace le fait de regarder ses comptes. Un
prélèvement nouveau, jamais vu, est invisible pour un détecteur de récurrences.

## Lecture seule, et pas par politesse

Le lien passe par l'agrément **DSP2 / AIS** du fournisseur (Enable Banking, palier
gratuit *Restricted Production*). AIS, c'est l'agrégation d'informations sur les comptes. L'initiation de
paiement est un agrément séparé (PIS), que ce jeton n'a pas et que la banque ne lui
ouvrira pas. **Le pire scénario d'une fuite est la lecture d'un historique, jamais un
mouvement d'argent.** C'est la seule raison pour laquelle ce dossier a le droit d'exister.

Trois conséquences pratiques :

- les identifiants du fournisseur vivent dans le **trousseau macOS**, pas dans un
  fichier — un fichier finit dans un tar de sauvegarde, un `cat` malheureux, un rsync
  vers un NAS. Pour Enable Banking, ça inclut la **clé privée RSA** : `bankread secrets
  --set` la lit, la range, et rappelle de supprimer le `.pem` téléchargé ;
- le consentement bancaire expire — **trois à six mois** selon ce que la banque accorde,
  et c'est sa réponse qui est enregistrée, pas notre demande. Rien ne peut le renouveler
  à ta place : il faut retourner sur le site de sa banque. `doctor` prévient à J-14 (pas
  à J-1, où un week-end suffirait à tout périmer) ;
- tout ça tourne **sur le Mac**. Pas dans une session cloud, pas dans un conteneur.

## Mise en route

```bash
# 0. créer l'application chez le fournisseur : https://enablebanking.com/sign-in/
#    Control Panel → API applications → environnement « Production », clé générée.
#    L'URL de retour à déclarer est http://127.0.0.1:8788/callback (à l'identique).

# 1. les deux identifiants (le second est le CHEMIN du .pem téléchargé)
bankread secrets --set          # ils vont dans le trousseau, pas sur le disque

# 2. trouver sa banque, puis signer le consentement (ouvre le site de la banque)
bankread banks bourso           # relève le NOM EXACT que renvoie l'API
bankread link "BoursoBank"      # puis, séparément :
bankread link "Caisse d'Epargne"

# 3. vérifier
bankread doctor
bankread project --days 45 --floor 300

# 4. le brief de 7 h 30 → tâches Todoist
launchd/install.sh
launchctl kickstart -k "gui/$(id -u)/com.bankread.brief-quotidien"   # essai immédiat
```

### Le cas BoursoBank, concrètement

C'est la banque contre laquelle ce dépôt a été écrit, donc voici ce à quoi s'attendre
plutôt qu'une généralité. Une autre banque se comportera autrement — et c'est
`bankread banks` qui fait foi, pas ce fichier :

- **~90 jours d'historique**, pas plus. Les échéances MENSUELLES — loyer, EDF,
  mensualisation des impôts — sortent dès la première lecture : trois passages suffisent.
  Les annuelles attendent que le registre ait accumulé (voir plus haut).
- **La validation passe par l'app BoursoBank** (authentification forte DSP2). Le
  parcours `bankread link` ouvre le site, puis le téléphone sonne. Il faut donc l'avoir
  sous la main, et refaire ce geste tous les 90 jours.
- **Ne pas compter sur l'agrégation Wicount 360.** BoursoBank agrège déjà le compte
  Caisse d'Épargne et envoie des alertes dessus, mais la DSP2 donne accès aux comptes
  *tenus par* la banque interrogée, pas à ce qu'elle agrège d'ailleurs. Le compte Caisse
  d'Épargne demande donc son propre `bankread link`, avec son propre consentement de
  90 jours. (À confirmer lors du premier branchement : si les comptes externes
  apparaissent dans le parcours, tant mieux.)
- **Les comptes joints** se choisissent dans l'écran de consentement de la banque. Ne
  cocher que ce qu'on veut vraiment lire : ce qui est coché part dans le registre local,
  et le registre ne s'oublie pas.

`bankread banks bourso` donne le nom exact — chez Enable Banking une banque se désigne
par son NOM et son pays, pas par un identifiant technique — et la durée de consentement
maximale que cette banque accorde. C'est cette valeur qui fait foi, pas ce fichier.

`link` enchaîne tout seul sur le **rapatriement de la fenêtre d'or** : l'historique
complet n'est servi que dans l'heure qui suit la signature, ensuite la banque retombe à
90 jours glissants. Ne pas interrompre cette étape — elle ne se rejoue qu'au prochain
renouvellement, dans trois à six mois.

## Les commandes

| | |
|---|---|
| `bankread doctor` | ce qui marche, ce qui va casser, quand. **Aucun appel réseau** — un diagnostic qui consomme du quota est un diagnostic qu'on n'ose pas lancer. |
| `bankread balances` | les soldes, avec leur âge |
| `bankread upcoming` | les échéances détectées et leur prochain passage |
| `bankread project` | **le croisement** : solde moins les échéances à venir, jour par jour |
| `bankread tx` | les dernières opérations |
| `bankread json` | tout d'un coup, pour un script |
| `bankread mcp` | serveur MCP sur stdio, six outils de lecture — pour Claude ou tout autre client MCP |

Codes de sortie : 0 tout va bien, 1 il y a à regarder, 2 échec dur — pour que launchd
et les scripts appelants s'y retrouvent.

### Faire lire les comptes par une IA

```bash
claude mcp add bankread -s user -- "$PWD/bankread" mcp
```

MCP est un protocole ouvert et `bankreadlib/mcp.py` est du JSON-RPC écrit à la main :
aucune bibliothèque d'éditeur, aucun jeton, aucun appel sortant. Le même serveur se
déclare de la même façon dans les autres clients MCP, et `bankread json` sert ce qui n'en
est pas. Le tout — y compris comment s'en passer complètement — est dans
[`docs/integration.md`](docs/integration.md).

## Deux limites payées d'avance, à ne pas redécouvrir

**Quatre appels par jour et par compte.** La plupart des banques plafonnent à quatre
appels par compte et par jour le rapatriement fait **en arrière-plan**. Quatre. Ce n'est
pas une limite qu'on frôle, c'est une limite qu'on atteint en une matinée de mise au
point. D'où un cache qui n'est pas un confort : sans lui, le brief du matin échoue un
jour sur deux. D'où aussi `--refresh`, qui est explicite et jamais le défaut.

Le plafond tombe quand un utilisateur est réellement devant l'écran, ce qu'un en-tête PSU
déclare à la banque. bankread ne l'envoie que si la commande a un terminal : le brief de
7 h 30 n'en a pas, donc il reste plafonné. C'est une déclaration faite à une banque, pas
un réglage de performance — la mettre à « vrai » depuis un agent launchd serait un
mensonge.

**Un historique court rend une projection OPTIMISTE, pas incomplète.** Toutes les banques
ne rendent pas treize mois — **BoursoBank en rend environ 90 jours**. En dessous de
380 jours, une échéance annuelle (taxe foncière, assurance, redevance) n'a pas pu être
vue passer deux fois, donc elle n'existe pas pour le détecteur, donc la trajectoire
annoncée est meilleure que la vraie. C'est le seul endroit où se tromper coûte de
l'argent, alors `upcoming`, `project` et `doctor` le disent en toutes lettres.

Et surtout, c'est pour ça que **`ledger.py` accumule au lieu de mettre en cache**. Le
premier jet (2026-08-20, matin) gardait un instantané : chaque lecture remplaçait la
précédente. Avec une banque à 90 jours, ça rendait une échéance annuelle non pas
« pas encore détectée » mais **indétectable à vie** — tout ce qui dépassait le trimestre
disparaissait de l'API et du cache en même temps. Le registre fond chaque lecture dans
un fichier durable ; au bout d'un an de briefs, bankread connaît 365 jours là où la
banque n'en montre que 90, et la taxe foncière apparaît à son deuxième passage.
`bankread doctor` affiche le compte à rebours.

## La règle du dépôt, appliquée à un solde

> Aucune ligne verte qui n'ait été observée.

Toute réponse de `read.py` porte un champ `etat` :

| `etat` | ce que ça veut dire |
|---|---|
| `observe` | lu à l'instant, ou assez récemment pour être encore vrai |
| `ancien` | servi du cache, avec son âge en clair — à lire, pas à croire |
| `inconnu` | on n'a rien d'assez frais pour prétendre quoi que ce soit |

Jamais un solde nu. Un `solde: null, etat: "inconnu"` **n'est pas un compte à zéro**, et
c'est la pire lecture possible d'un chiffre manquant — les descriptions des outils MCP le
répètent au modèle avant qu'il appelle, pour qu'il ne l'apprenne pas en se trompant.

Même discipline sur les prédictions : une échéance vue deux fois n'est pas une échéance,
c'est une coïncidence. Elle sort marquée `confidence: "faible"` et n'entre pas dans la
projection. Trois passages réguliers, c'est un fait.

Et sur les rentrées : projeter les seules sorties donne une trajectoire qui plonge
toujours, donc une alarme tous les jours, donc plus d'alarme du tout au bout d'une
semaine. Le salaire et les allocations sont détectés par le même chemin et comptés dans
l'autre sens. `test_le_salaire_qui_arrive_avant_evite_la_fausse_alerte` garde ce cas.

## Le brief de 7 h 30

`brief/brief.md` est le texte que Claude exécute chaque matin, lancé par launchd via
`brief/run-brief`. Il lit Gmail et la banque, et sa seule sortie utile est un petit
nombre de tâches Todoist étiquetées `auto-brief`.

**Le silence est une réussite.** Un matin sans rien à signaler produit zéro tâche — pas
de résumé quotidien, pas de « rien à signaler ». Cinq motifs seulement créent une tâche,
et le carnet `~/.config/bankread/brief-vu.json` empêche qu'un même colis en retard en
génère une par jour pendant deux semaines.

`run-brief` passe une liste FERMÉE d'outils à `claude --allowedTools`. Aucun outil
d'écriture Gmail n'y figure : le brief lit le courrier, il n'y répond jamais. Et rien
n'est laissé à une demande d'autorisation, puisque personne n'est devant l'écran pour y
répondre à 7 h 30.

Le modèle appelé est un **pilote remplaçable** (`brief/agents/`), pas un appel en dur :
`BANKREAD_AGENT=aucun brief/run-brief` fait tourner le brief sans aucune IA, et la
projection tombe quand même dans le journal. La lecture des comptes n'a jamais eu besoin
d'un modèle — voir [`docs/integration.md`](docs/integration.md).

`launchd/install.sh` n'installe **que** cet agent. Un script d'installation qui en fait
plus que son nom finit par réinstaller quelque chose de mort, et on passe la soirée à
chercher pourquoi deux exemplaires tournent.

## Changer de fournisseur

Le couplage est mince, et c'est vérifié par un test (`test_le_contrat_tient_en_deux_methodes`) :

| | dépend du fournisseur ? |
|---|---|
| `bankreadlib/enablebanking.py`, `bankreadlib/gocardless.py` | **oui** — jetons, quotas, HTTP, parcours de consentement |
| `bankreadlib/provider.py` | il choisit lequel charger, et écrit le contrat en `Protocol` |
| `bankread` (sous-commandes `banks` / `link`) | oui, partiellement — le parcours de liaison |
| `ledger.py`, `recurring.py`, `read.py`, `mcp.py`, `brief/`, `launchd/` | non, et plus une seule importation directe |

Pour brancher un troisième fournisseur, il suffit d'un module qui expose ces deux
méthodes — le `Protocol` de `provider.py` les déclare, et `provider.charger()` en fait le
choix :

```python
balances(account_id)                          -> {"balances": [...]}
transactions(account_id, date_from, date_to)  -> {"transactions": {"booked": [...]}}
```

Le format attendu est celui du **Groupe de Berlin** : `balanceType` / `balanceAmount` pour
les soldes, `bookingDate` et `transactionAmount` **signé** pour les opérations. Si le
fournisseur parle autre chose, la traduction se paie dans son client et nulle part
ailleurs — voir `enablebanking._operation()`, qui rend leur signe à des montants
qu'Enable Banking livre toujours positifs.

Trois pièges rencontrés en écrivant le client Enable Banking, à ne pas redécouvrir :

- **le signe des montants** est porté à côté du montant (`credit_debit_indicator`), pas
  dedans. Le recopier tel quel ferait compter chaque prélèvement comme une rentrée ;
- **l'historique complet ne dure qu'une heure** après la signature (voir plus haut) ;
- **la durée du consentement accordée** peut être plus courte que celle demandée : c'est
  la réponse de la banque qu'on enregistre, sinon `doctor` annonce un consentement valide
  deux mois après sa mort.

## Tests

```bash
python3 test_bankread.py
```

51 tests, stdlib seule, aucun réseau. Ils vérifient surtout **les cas où le code doit se
taire** : deux occurrences ne font pas une échéance, un cache de trente heures n'est plus
un solde, on ne projette pas sur un solde jamais observé. Une détection qui se trompe de
date ne plante pas — elle annonce les impôts le 12 au lieu du 15, avec le même aplomb.

## Ce qui n'est pas versionné

`~/.config/bankread/state.json` (comptes liés, jetons, dates de consentement) et
`~/.cache/bankread/` — spécifiques à cette machine et à ce consentement. Les identifiants
du fournisseur ne sont nulle part sur le disque : ils sont dans le trousseau macOS,
service `bankread-enablebanking` (ou `bankread-gocardless`), **clé privée RSA comprise**.

⚠️ **`~/.local/share/bankread/ledger/` est à part : c'est le registre, et il ne se purge
pas.** Ce qu'il contient, la banque ne peut plus le redonner — au-delà de 90 jours, elle
a oublié. Le supprimer avec le cache, c'est repartir à trois mois de mémoire. Il est
rangé dans les *données* et pas dans le *cache* exactement pour rendre cette confusion
difficile. Contrepartie assumée : c'est un an d'opérations bancaires en clair sur le
disque, en 0600.
