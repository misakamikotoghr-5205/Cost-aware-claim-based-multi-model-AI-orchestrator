param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force $OutputDirectory | Out-Null
$results = @()
foreach ($program in @('Word.Application', 'Excel.Application')) {
  $record = [ordered]@{ program = $program; registered = $false; launched = $false; status = 'unavailable' }
  $office = $null
  try {
    $registered = [type]::GetTypeFromProgID($program)
    $record.registered = $null -ne $registered
    if ($record.registered) {
      $office = New-Object -ComObject $program
      $record.launched = $true
      $record.status = 'installed_and_com_activatable'
      # Availability only. No real document or account is opened or changed.
      $record.version = $office.Version
    }
  } catch {
    $record.status = 'activation_failed'
    $record.error_type = $_.Exception.GetType().FullName
  } finally {
    if ($null -ne $office) {
      $office.Quit()
      [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($office)
    }
  }
  $results += $record
}
[ordered]@{
  platform = 'Windows'
  checked_at = (Get-Date).ToUniversalTime().ToString('o')
  programs = $results
  note = 'COM availability probe only. This does not establish document-editing or Office integration acceptance.'
} | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 (Join-Path $OutputDirectory 'office-availability.json')
