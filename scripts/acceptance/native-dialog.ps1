param([Parameter(Mandatory=$true)][int]$ApplicationPid,
      [Parameter(Mandatory=$true)][string]$DiagnosticsPath)
$ErrorActionPreference = 'Stop'
$diagnostic = [ordered]@{ expected_pid = $ApplicationPid; windows = @(); invoked = $false }
try {
  Add-Type -AssemblyName UIAutomationClient
  Add-Type -AssemblyName UIAutomationTypes
  $desktop = [Windows.Automation.AutomationElement]::RootElement
  $deadline = (Get-Date).AddSeconds(20)
  do {
    $windows = $desktop.FindAll([Windows.Automation.TreeScope]::Children, [Windows.Automation.Condition]::TrueCondition)
    foreach ($window in $windows) {
      if ($window.Current.ProcessId -ne $ApplicationPid -and $window.Current.Name -ne 'Select a screen') { continue }
      $condition = [Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ControlTypeProperty, [Windows.Automation.ControlType]::Button)
      $buttons = $window.FindAll([Windows.Automation.TreeScope]::Descendants, $condition)
      $row = [ordered]@{ title = $window.Current.Name; pid = $window.Current.ProcessId; buttons = @($buttons | ForEach-Object { $_.Current.Name }) }
      $diagnostic.windows = @($diagnostic.windows | Where-Object { $_.title -ne $row.title }) + @($row)
      if ($window.Current.Name -ne 'Select a screen') { continue }
      foreach ($button in $buttons) {
        # Invoke the actual native dialog control. Never replace the dialog API.
        $buttonName = $button.Current.Name
        if ($buttonName -match 'Screen|Display|Entire') {
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
  $diagnostic | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 $DiagnosticsPath
}
