param([Parameter(Mandatory=$true)][int]$ApplicationPid)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$desktop = [Windows.Automation.AutomationElement]::RootElement
$deadline = (Get-Date).AddSeconds(20)
do {
  $windows = $desktop.FindAll([Windows.Automation.TreeScope]::Children, [Windows.Automation.Condition]::TrueCondition)
  foreach ($window in $windows) {
    if ($window.Current.ProcessId -ne $ApplicationPid -or $window.Current.Name -ne 'Select a screen') { continue }
    $condition = New-Object Windows.Automation.PropertyCondition ([Windows.Automation.AutomationElement]::ControlTypeProperty), ([Windows.Automation.ControlType]::Button)
    $buttons = $window.FindAll([Windows.Automation.TreeScope]::Descendants, $condition)
    foreach ($button in $buttons) {
      # Invoke the actual native dialog control. Never replace the dialog API.
      if ($button.Current.Name -match 'Screen|Display|Entire') {
        $invoke = $button.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern)
        $invoke.Invoke()
        exit 0
      }
    }
  }
  Start-Sleep -Milliseconds 200
} while ((Get-Date) -lt $deadline)
throw 'No screen button was found in the actual native selection dialog.'
