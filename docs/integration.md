# Brancher une IA — et s'en passer

Trois questions qui n'en font qu'une : **qu'est-ce qui, dans ce dépôt, dépend d'un
modèle ?** Réponse courte : le brief du matin, et rien d'autre. La lecture des comptes,
le registre, la détection d'échéances et la projection n'ont jamais eu besoin d'une IA —
ce sont des soustractions.

## 1. Faire lire les comptes par Claude

`bankread mcp` est un serveur **MCP** sur stdio : six outils, tous en lecture. Une seule
commande pour le brancher, une fois pour toutes :

```bash
claude mcp add bankread -s user -- /chemin/vers/bankread mcp
claude mcp list          # doit afficher : bankread … ✔ Connected
```

`-s user` le rend disponible dans **toutes** les sessions, quel que soit le dossier de
lancement ; `-s project` le limiterait à un dépôt. Pour l'enlever : `claude mcp remove
bankread -s user`.

Ensuite, dans n'importe quelle session : « quel sera mon solde le 15 ? », « qu'est-ce qui
tombe cette semaine ? ». Le modèle appelle `banque_projection` ou `banque_echeances` et
lit la réponse.

⚠️ **Ce que le modèle doit comprendre avant d'appeler**, et que les descriptions d'outils
lui répètent : `solde: null, etat: "inconnu"` **n'est pas un compte à zéro**. C'est
« je n'ai rien d'assez frais pour prétendre quoi que ce soit ». Un modèle qui lit ça comme
un solde nul annonce une catastrophe qui n'existe pas — c'est le seul contresens qui
compte ici, d'où sa présence dans chaque description plutôt que dans ce fichier seul.

## 2. Décorréler de Claude

**Le serveur MCP n'est déjà pas lié à Claude.** MCP est un protocole ouvert, et
`bankreadlib/mcp.py` est du JSON-RPC écrit à la main : aucune bibliothèque d'éditeur,
aucun jeton, aucun appel sortant. Tout client MCP le lit — la section 3 en donne la liste.

Ce qui l'était vraiment, c'est **le brief** : `brief/run-brief` appelait `claude` en dur,
avec ses drapeaux. C'est désormais un pilote remplaçable :

```
brief/run-brief              prépare la configuration MCP, la liste d'outils, le journal
brief/agents/claude.sh       le pilote par défaut
brief/agents/aucun.sh        aucun modèle du tout — la projection, et c'est tout
```

```bash
BANKREAD_AGENT=aucun brief/run-brief     # le brief sans IA, pour voir
```

Le contrat d'un pilote tient en trois arguments : le fichier de consignes, la
configuration MCP (chemins déjà résolus), la liste fermée d'outils autorisés. Pour en
ajouter un, copier `claude.sh` et remplacer la dernière ligne.

⚠️ Les drapeaux des autres agents en ligne de commande (`codex`, `gemini`, …) **n'ont pas
été vérifiés ici** : leurs équivalents de `--mcp-config` et `--allowedTools` sont à lire
dans leur documentation avant d'écrire le pilote. Un pilote écrit de mémoire échouerait à
7 h 30, sans personne pour le voir.

`aucun.sh` mérite un mot : il ne remplace pas le brief, il prouve une propriété. Lire le
courrier et reconnaître un colis en retard suppose de comprendre du texte libre — c'est
pour ça que le modèle est là. Mais « le solde de fin de mois passe-t-il sous le
plancher ? » est une soustraction, et elle continue de tomber dans le journal quand
Claude est en panne ou le quota épuisé.

## 3. En faire un projet public, utilisable avec d'autres IA

### Ce qui marche déjà partout

MCP est implémenté par Claude (Desktop, Code), ChatGPT et les SDK d'agents d'OpenAI, VS
Code, Cursor, Zed, Continue, LibreChat, n8n, et une longue liste d'autres. La déclaration
est la même partout — un objet `mcpServers` :

```json
{
  "mcpServers": {
    "bankread": { "command": "/chemin/vers/bankread", "args": ["mcp"] }
  }
}
```

Et pour ce qui n'est pas MCP du tout, `bankread json` crache l'ensemble sur stdout : un
script, un cron, une feuille de calcul, un modèle appelé à la main s'en accommodent sans
rien connaître du dépôt.

### Ce qu'il faut faire AVANT de rendre public

Rien de tout cela n'est un détail de publication : ce sont des choses qui, une fois
poussées, ne se rattrapent pas.

1. **Purger le personnel.** ✅ fait le 2026-08-20. Le dépôt nommait son auteur, sa
   banque, sa famille, son projet Todoist et son quotidien dans `brief/brief.md`. Aucun
   secret là-dedans, mais c'était une biographie. Le geste retenu n'a pas été de
   caviarder : les consignes du brief sont devenues **génériques**, et tout ce qui est
   propre à une personne vit dans `brief/brief.local.md`, qui n'est pas versionné (voir
   `brief/brief.local.example.md`).
2. **Vérifier l'historique, pas seulement les fichiers.** ✅ Un fichier nettoyé dans un
   commit ultérieur reste lisible en entier dans `git log -p` — donc nettoyer l'arbre ne
   suffisait pas. L'historique a été remplacé par un commit unique au moment de
   l'ouverture. À refaire de tête à chaque fois que le dépôt redevient public après une
   phase privée.
3. **Une licence.** ✅ MIT. Permissive, et surtout **la plus courante** : sur un dépôt
   qu'on publie pour être repris, la familiarité de la licence vaut mieux que sa
   perfection juridique — un lecteur la reconnaît sans la lire. Sa seule condition est de
   conserver la mention de copyright. Sans licence du tout, « public » voudrait dire
   « visible », pas « réutilisable ».
4. **Dire ce que l'outil ne peut pas faire**, en tête de README ✅ : agrément AIS, aucune
   initiation de paiement, secrets dans le trousseau, registre en clair sur le disque.
   Un outil qui touche à de l'argent est jugé sur ce qu'il refuse de faire.
5. **Chacun son application Enable Banking.** ✅ Il n'y a rien à mutualiser : l'identifiant
   et la clé privée sont personnels, et le palier gratuit ne lit que les comptes qu'on a
   soi-même déclarés. Le README doit donc commencer par l'inscription, pas par
   `git clone` — sinon la première impression est « ça ne marche pas ».
6. **L'anglais pour la partie technique.** ✅ `README.en.md`. Le français est la voix de ce dépôt et il n'y
   a pas de raison de la perdre ; mais un `README.en.md` court, factuel, décide de qui
   peut s'en servir.

### Packaging (pas fait, et pas urgent)

`pipx install bankread` ou `uvx bankread` supposent un `pyproject.toml` et un point
d'entrée — donc de déplacer le script `bankread` dans le paquet. Zéro dépendance rend
l'opération triviale le jour où quelqu'un d'autre l'installe. Le nom
`bankread` était libre sur PyPI au 2026-08-20 (l'API y répond 404).

Avant ça, `git clone` + `./bankread` suffit, et c'est honnête : un outil qui lit des
comptes bancaires se lit avant de s'installer.
