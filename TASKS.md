# À faire

Ce fichier ne contient que ce qui reste **à faire ou à décider**. Une ligne qui est faite
disparaît ; quand il n'y a plus rien, le fichier est supprimé.

## Bloqué sur un geste humain

- ☐ **Créer l'application Enable Banking** (Control Panel → API applications, environnement
  Production, clé RSA générée), puis `bankread secrets --set`, puis `bankread link`.
  → URL de retour à déclarer, au caractère près : `http://127.0.0.1:8788/callback`
  → *Rien ci-dessous ne peut être vérifié avant ça : tout le client Enable Banking n'a
  jamais parlé à un vrai serveur.*

## À vérifier à la PREMIÈRE lecture réelle — dans l'ordre, le jour du premier `link`

- ☐ **Le code de débit : `DBIT` ou `DBDT` ?** La documentation écrit les deux. Le code ne
  reconnaît que `CRDT` et traite tout le reste comme une sortie, donc il est correct dans
  les deux cas — mais la valeur réelle mérite d'être notée dans `enablebanking.py`.
  → se lit dans la réponse brute de `/accounts/{uid}/transactions`
- ☐ **La fenêtre d'or rend-elle vraiment plus de 90 jours ?** `link` enchaîne sur le
  rapatriement profond ; `bankread doctor` doit ensuite afficher un registre de plusieurs
  centaines de jours, pas 90. Si c'est 90, la fenêtre n'a pas été exploitée et il faut
  comprendre pourquoi AVANT le prochain renouvellement — elle ne se rejoue pas.
- ☐ **La durée de consentement réellement accordée** par la banque (`maximum_consent_validity`
  puis `access.valid_until`). Le README parle de « trois à six mois » : le remplacer par la
  vraie valeur une fois connue.
- ☐ **Les comptes joints apparaissent-ils** dans l'écran de consentement, et sous quel nom ?
- ☐ **Quel type de solde** la banque renvoie-t-elle (`ITAV`, `CLAV`, `CLBD` … ) ? L'ordre de
  préférence de `read._meilleur_solde()` a été écrit sans jamais voir une vraie réponse.

## À décider

- 🤔 **Publier sur PyPI ?** Le nom `openbanking-mcp` était libre au 2026-08-20. L'installation
  depuis git suffit tant que personne ne le réclame ; publier ajoute une chaîne de sortie à
  tenir à jour.
- 🤔 **Un pilote pour un autre agent** (`codex`, `gemini` …) dans `brief/agents/` ? Le contrat
  tient en trois arguments. Rien ne sera écrit sans avoir lu leur documentation : un pilote
  écrit de mémoire échouerait à 7 h 30, sans personne pour le voir.
