$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  Write-Host "Node.js is required. Install it from https://nodejs.org/ and run this file again."
  Read-Host "Press Enter to close"
  exit 1
}

if (-not (Test-Path -LiteralPath "node_modules")) {
  npm install
}

if (-not (Test-Path -LiteralPath "config.json")) {
  Copy-Item -LiteralPath "config.example.json" -Destination "config.json"
  Write-Host "Created config.json from config.example.json."
}

npm run install-browser

Write-Host ""
Write-Host "Choose an action:"
Write-Host "1) First login / refresh login"
Write-Host "2) Export yesterday's active GMV Max creative reports"
Write-Host "3) Inspect page for troubleshooting"
$choice = Read-Host "Enter 1, 2, or 3"

switch ($choice) {
  "1" {
    $env:TIKTOK_ADS_EMAIL = Read-Host "TikTok Ads email"
    $securePassword = Read-Host "TikTok Ads password" -AsSecureString
    $env:TIKTOK_ADS_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
      [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    )
    npm run assisted-login
  }
  "2" {
    npm run export-gmvmax-creatives
  }
  "3" {
    npm run inspect-page
  }
  default {
    Write-Host "Invalid choice."
    exit 1
  }
}

Write-Host ""
Read-Host "Done. Press Enter to close"
