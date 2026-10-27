#!/usr/bin/env bash
set -euo pipefail

host="${DEPLOY_HOST:-deploy.internal}"
release="$(git rev-parse --short HEAD)"

echo "Deploying $release to $host"
rsync -az --delete ./dist/ "deploy@$host:/srv/app/releases/$release/"
ssh "deploy@$host" "ln -sfn /srv/app/releases/$release /srv/app/current && sudo systemctl reload app"
