param([Parameter(Mandatory=$true)][int]$ApplicationPid,
      [Parameter(Mandatory=$true)][string]$DiagnosticsPath,
      [ValidateRange(3,60)][int]$DeadlineSeconds = 60)
$ErrorActionPreference = 'Stop'
$diagnostic = [ordered]@{ expected_pid = $ApplicationPid; windows = @(); invoked = $false }
function Save-DesktopDiagnostic {
  try {
    $snapshot = Join-Path (Split-Path $DiagnosticsPath) (([IO.Path]::GetFileNameWithoutExtension($DiagnosticsPath)) + '-desktop.png')
    & (Join-Path $PSScriptRoot 'windows-dialog-desktop.ps1') -ApplicationPid $ApplicationPid -ReportPath ($snapshot + '.json') -ImagePath $snapshot
    $diagnostic.desktop_diagnostic = [IO.Path]::GetFileName($snapshot)
  } catch { $diagnostic.desktop_diagnostic_error_type = $_.Exception.GetType().FullName }
}
try {
  Add-Type -AssemblyName UIAutomationClient
  Add-Type -AssemblyName UIAutomationTypes
  $desktop = [Windows.Automation.AutomationElement]::RootElement
  $deadline = (Get-Date).AddSeconds($DeadlineSeconds)
  do {
    $windows = $desktop.FindAll([Windows.Automation.TreeScope]::Children, [Windows.Automation.Condition]::TrueCondition)
    foreach ($window in $windows) {
      $condition = [Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ControlTypeProperty, [Windows.Automation.ControlType]::Button)
      $buttons = $window.FindAll([Windows.Automation.TreeScope]::Descendants, $condition)
      $row = [ordered]@{ hwnd = $window.Current.NativeWindowHandle; title = $window.Current.Name; pid = $window.Current.ProcessId; class = $window.Current.ClassName; offscreen = $window.Current.IsOffscreen; enabled = $window.Current.IsEnabled; buttons = @($buttons | ForEach-Object { $_.Current.Name }) }
      $diagnostic.windows = @($diagnostic.windows | Where-Object { $_.hwnd -ne $row.hwnd }) + @($row)
      # A Windows task dialog may expose the application name as its UIA name.
      # Identify the real prompt and controls, and keep each distinct HWND.
      if ($window.Current.ProcessId -ne $ApplicationPid) { continue }
      $content = $window.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition)
      $hasPrompt = @($content | Where-Object { $_.Current.Name -eq 'Choose a screen to capture for local region selection.' }).Count -gt 0
      if (-not $hasPrompt -or $row.buttons -notcontains 'Cancel') { continue }
      foreach ($button in $buttons) {
        # Invoke the actual native dialog control. Never replace the dialog API.
        $buttonName = $button.Current.Name
        if ($buttonName -match 'Screen|Display|Entire') {
          Save-DesktopDiagnostic
          $invoke = $button.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern)
          $invoke.Invoke()
          $diagnostic.invoked = $true
          $diagnostic.selected_button = $buttonName
          exit 0
        }
      }
    }
    Start-Sleep -Milliseconds 200
  } while ((Get-Date) -lt $deadline)
  throw 'No screen button was found in the actual native selection dialog.'
} catch {
  $diagnostic.error_type = $_.Exception.GetType().FullName
  $diagnostic.error = $_.Exception.Message
  throw
} finally {
  if (-not $diagnostic.desktop_diagnostic) { Save-DesktopDiagnostic }
  $diagnostic | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 $DiagnosticsPath
}
