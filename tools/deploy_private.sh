#!/bin/sh
# Deploy the private edition to Vercel (project "the-margin-private"). Copies the shared renderer into private/margin first.
# Secrets are NOT handled here: they are entered in the Vercel dashboard (see README › Private edition).
set -e
cd "$(dirname "$0")/../private"
rm -rf margin && mkdir margin
cp ../margin/__init__.py ../margin/build.py ../margin/select.py ../margin/ingest.py ../margin/notion.py margin/
npx -y vercel@latest deploy --prod --yes "$@"
