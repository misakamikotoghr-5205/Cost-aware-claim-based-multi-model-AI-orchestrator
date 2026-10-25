param([Parameter(Mandatory=$true)][string]$Repository,
      [Parameter(Mandatory=$true)][string]$Source,
      [Parameter(Mandatory=$true)][string]$Launcher,
      [Parameter(Mandatory=$true)][string]$ProfileDirectory,
      [Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$env:HERMES_HOME = $ProfileDirectory
$env:HERMES_RUNTIME_DIR = Join-Path $ProfileDirectory 'tools'
$spec = Get-Content (Join-Path $Repository 'integrations/hermes-client/upstream.json') -Raw | ConvertFrom-Json
$version = $spec.core_version
# A labelled offline endpoint only enables the native draft surface. No server,
# provider response, login or inference is simulated, and no prompt is sent.
$configuration = @{
  nous = @{ guest = $false }; onboarding = @{ run = $false }
  model = @{ default = 'offline-acceptance'; provider = 'offline-acceptance' }
  providers = @{ 'offline-acceptance' = @{
    api = 'http://127.0.0.1:19997/v1'; name = '离线验收（未调用模型）'
    api_mode = 'chat_completions'; key_env = 'ACCEPTANCE_ONLY_API_KEY'
    models = @{ 'offline-acceptance' = @{} }
  } }
  auxiliary = @{ title_generation = @{ enabled = $false } }
  approvals = @{ mode = 'manual' }
}
$configuration | ConvertTo-Json -Depth 12 | Set-Content -Encoding utf8 (Join-Path $ProfileDirectory 'config.yaml')
'ACCEPTANCE_ONLY_API_KEY=isolated-no-inference' | Set-Content -Encoding ascii (Join-Path $ProfileDirectory '.env')
$coreArchive = Join-Path $Repository "dist/extensions/orchestrator-core-$version.zip"
$preparedCoreSha = (Get-FileHash $coreArchive -Algorithm SHA256).Hash
python (Join-Path $Repository 'scripts/build_extensions.py') --ids all --upstream $Source
if ($LASTEXITCODE -ne 0) { throw 'Independent extension ZIP build failed.' }
if ((Get-FileHash $coreArchive -Algorithm SHA256).Hash -ne $preparedCoreSha) {
  throw 'Repeated native core compilation changed the installation ZIP.'
}
$pointerArchive = Join-Path $Repository "dist/extensions/smart-pointer-$version.zip"
python -m src.extension_packages --hermes-home $ProfileDirectory install $pointerArchive
if ($LASTEXITCODE -ne 0) { throw 'Actual optional pointer ZIP installation failed.' }
foreach ($plugin in @('orchestrator-core', 'smart-pointer')) {
  & $Launcher --profile default plugins enable $plugin
  if ($LASTEXITCODE -ne 0) { throw "Native plugin admission failed for $plugin." }
}
$skillArchive = Join-Path $env:RUNNER_TEMP 'misaka-agent-skill.zip'
python (Join-Path $Repository 'scripts/build_agent_skill.py') --output $skillArchive
if ($LASTEXITCODE -ne 0) { throw 'Standalone Agent Skill ZIP build failed.' }
$skillDirectory = Join-Path $ProfileDirectory 'skills'
Expand-Archive -Path $skillArchive -DestinationPath $skillDirectory -Force
$cli = Join-Path $skillDirectory 'misaka-agent/scripts/misaka.py'
$connectionConfig = Join-Path $env:RUNNER_TEMP 'misaka-acceptance-connections.json'
$setupLines = python $cli --config $connectionConfig --connection acceptance --launcher $Launcher --root $Source --home $ProfileDirectory setup
if ($LASTEXITCODE -ne 0) { throw 'Actual extracted Skill setup failed.' }
$setupResult = ($setupLines -join [Environment]::NewLine) | ConvertFrom-Json
if (-not $setupResult.ok -or $setupResult.contains_credentials -ne $false) { throw 'Skill setup did not confirm a credential-free named connection.' }
$report = [ordered]@{
  skill_archive_sha256 = (Get-FileHash $skillArchive -Algorithm SHA256).Hash.ToLowerInvariant()
  actual_zip_installed = $true; same_native_launcher_and_home = $true
  model_inference = 'not_invoked'; real_provider_credentials = 'not_used'
  labelled_offline_endpoint_for_unsent_draft_only = $true
  commands = @{}
}
foreach ($command in @('status', 'extensions', 'quota')) {
  $lines = python $cli --config $connectionConfig --connection acceptance --profile default $command
  $commandExit = $LASTEXITCODE
  $payload = ($lines -join [Environment]::NewLine)
  $result = $payload | ConvertFrom-Json
  $payload | Set-Content -Encoding utf8 (Join-Path $OutputDirectory "skill-$command.json")
  if ($commandExit -ne 0 -or -not $result.ok) { throw "Actual native Agent Skill reported unavailable: $command." }
  $report.commands[$command] = @{ ok = $true; output = "skill-$command.json" }
}
$report | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $OutputDirectory 'skill-native-report.json')
