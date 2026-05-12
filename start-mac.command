#!/bin/bash
set -e

cd "$(dirname "$0")"

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js is required. Install it from https://nodejs.org/ and run this file again."
  read -n 1 -s -r -p "Press any key to close..."
  exit 1
fi

if [ ! -d "node_modules" ]; then
  npm install
fi

if [ ! -f "config.json" ]; then
  cp config.example.json config.json
  echo "Created config.json from config.example.json."
fi

npm run install-browser

echo
echo "Choose an action:"
echo "1) First login / refresh login"
echo "2) Export yesterday's active GMV Max creative reports"
echo "3) Inspect page for troubleshooting"
read -r -p "Enter 1, 2, or 3: " choice

case "$choice" in
  1)
    read -r -p "TikTok Ads email: " TIKTOK_ADS_EMAIL
    read -r -s -p "TikTok Ads password: " TIKTOK_ADS_PASSWORD
    echo
    export TIKTOK_ADS_EMAIL
    export TIKTOK_ADS_PASSWORD
    npm run assisted-login
    ;;
  2)
    npm run export-gmvmax-creatives
    ;;
  3)
    npm run inspect-page
    ;;
  *)
    echo "Invalid choice."
    exit 1
    ;;
esac

echo
read -n 1 -s -r -p "Done. Press any key to close..."
