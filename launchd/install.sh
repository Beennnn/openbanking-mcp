#!/bin/bash
# Installe l'agent du brief quotidien :
#   com.bankread.brief-quotidien — lance brief/run-brief tous les jours à 7 h 30
#
#   ./launchd/install.sh          # installer + charger
#   ./launchd/install.sh remove   # décharger + désinstaller
#
# UN AGENT, UN SCRIPT. Ce fichier n'installe que com.bankread.brief-quotidien, et rien
# d'autre ne doit s'y ajouter. Un script d'installation qui en fait plus que son nom
# finit par réinstaller quelque chose de mort — et on passe la soirée à chercher pourquoi
# deux exemplaires tournent. Leçon payée ailleurs, pas à repayer ici.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.bankread.brief-quotidien
UID_="$(id -u)"

if [ "${1:-}" = "remove" ]; then
  launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
  echo "✔ agent retiré — plus aucune tâche ne sera créée"
  exit 0
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "✖ \`claude\` introuvable dans le PATH. Le brief ne peut pas tourner sans lui." >&2
  exit 1
fi

# Un consentement bancaire absent ne bloque pas l'installation : le brief sait vivre
# avec (il se contente de Gmail et le dit). Mais autant le signaler tout de suite.
if ! "$REPO/bankread" doctor >/dev/null 2>&1; then
  echo "⚠ bankread signale quelque chose — \`bankread doctor\` pour voir quoi."
  echo "  Le brief tournera quand même, sur Gmail seul."
fi

mkdir -p "$REPO/logs"
sed "s|__REPO__|$REPO|g" "$REPO/launchd/$LABEL.plist" \
  > "$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_" "$HOME/Library/LaunchAgents/$LABEL.plist"

echo "✔ brief quotidien installé — tous les jours à 7 h 30"
echo "  essai immédiat : launchctl kickstart -k gui/$UID_/$LABEL"
echo "  log            : $REPO/logs/brief.log"
echo "  arrêt          : ./launchd/install.sh remove"
