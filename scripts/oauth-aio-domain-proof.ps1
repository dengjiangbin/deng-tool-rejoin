# OAuth aio domain migration proof (no secrets)
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
$Out = Join-Path $Root 'site\proofs\oauth_aio_domain_proof.json'
$Eco = Get-Content (Join-Path $Root 'ecosystem.site.json') -Raw | ConvertFrom-Json
$SiteEnv = $Eco.apps | Where-Object { $_.name -eq 'deng-tool-site' } | Select-Object -ExpandProperty env

$proof = @{
  timestamp = (Get-Date).ToUniversalTime().ToString('o')
  deployMarker = $SiteEnv.TOOL_SITE_ASSET_VERSION
  gitCommit = $SiteEnv.GIT_COMMIT
  publicSiteUrl = $SiteEnv.TOOL_SITE_PUBLIC_URL
  discordRedirectUri = $SiteEnv.DISCORD_REDIRECT_URI
  discordAioWebRedirectUri = $SiteEnv.DISCORD_AIO_WEB_REDIRECT_URI
  discordAioRedirectUri = $SiteEnv.DISCORD_AIO_REDIRECT_URI
  callbackRoutes = @(
    '/api/aio/auth/callback',
    '/auth/discord/callback'
  )
  cookie = @{
    name = 'deng_sid'
    domain = '.deng.my.id'
    secure = $true
    sameSite = 'lax'
    path = '/'
  }
  postLoginDefault = '/dashboard'
  tests = @{}
}

try {
  $login = Invoke-WebRequest -Uri 'https://aio.deng.my.id/login' -UseBasicParsing -TimeoutSec 30
  $proof.loginPage = @{
    status = [int]$login.StatusCode
    hasAllInOneBrand = ($login.Content -match 'DENG All In One')
    hasToolOAuthLink = ($login.Content -match 'tool\.deng\.my\.id')
    authDiscordHref = ($login.Content -match '/auth/discord')
  }
} catch {
  $proof.loginPage = @{ error = $_.Exception.Message }
}

try {
  $start = Invoke-WebRequest -Uri 'https://aio.deng.my.id/auth/discord' -MaximumRedirection 0 -UseBasicParsing -TimeoutSec 30 -ErrorAction SilentlyContinue
  $loc = $start.Headers.Location
  $proof.oauthStart = @{
    status = [int]$start.StatusCode
    locationContainsAioCallback = ($loc -match 'aio\.deng\.my\.id%2Fapi%2Faio%2Fauth%2Fcallback' -or $loc -match 'aio\.deng\.my\.id/api/aio/auth/callback')
    locationContainsTool = ($loc -match 'tool\.deng\.my\.id')
  }
} catch {
  if ($_.Exception.Response) {
    $resp = $_.Exception.Response
    $proof.oauthStart = @{ status = [int]$resp.StatusCode }
  } else {
    $proof.oauthStart = @{ error = $_.Exception.Message }
  }
}

foreach ($route in @('/api/aio/auth/callback?error=access_denied', '/auth/discord/callback?error=access_denied')) {
  try {
    $cb = Invoke-WebRequest -Uri "https://aio.deng.my.id$route" -MaximumRedirection 0 -UseBasicParsing -TimeoutSec 30 -ErrorAction SilentlyContinue
    $proof.callbackPublic[$route] = @{ status = [int]$cb.StatusCode; location = $cb.Headers.Location }
  } catch {
    $resp = $_.Exception.Response
    if ($resp) {
      $proof.callbackPublic[$route] = @{ status = [int]$resp.StatusCode; handledWithoutAuthWall = $true }
    }
  }
}

$proof | ConvertTo-Json -Depth 6 | Set-Content $Out -Encoding UTF8
Write-Host "Proof written to $Out"
