#!/bin/bash
# Pilote « claude » — le brief tel qu'il tourne depuis le premier jour.
#
# Reçoit trois arguments, et c'est tout le contrat d'un pilote :
#   $1  le fichier de consignes (brief/brief.md)
#   $2  la configuration MCP, chemins déjà résolus
#   $3  les outils autorisés, séparés par des virgules
#
# `--allowedTools` est une liste FERMÉE, et c'est le point. Ce script tourne sans
# personne pour répondre à une demande d'autorisation : ce qui n'est pas listé ne peut
# pas arriver, plutôt que de bloquer le brief en attendant un clic qui ne viendra pas.
# Aucun outil d'écriture Gmail n'y figure — le brief lit le courrier, il n'y répond jamais.
set -euo pipefail

exec claude -p "$(cat "$1")" \
  --mcp-config "$2" \
  --allowedTools "$3" \
  --permission-mode acceptEdits \
  --add-dir "$HOME/.config/bankread" \
  --output-format text
