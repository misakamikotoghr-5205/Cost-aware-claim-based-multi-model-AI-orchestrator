param([Parameter(Mandatory=$true)][int]$ApplicationPid,
      [Parameter(Mandatory=$true)][string]$DiagnosticsPath,
      [ValidateRange(3,60)][int]$DeadlineSeconds = 60)
$ErrorActionPreference = 'Stop'
$diagnostic = [ordered]@{ expected_pid = $ApplicationPid; windows = @(); invoked = $false }
$previousContext = [IntPtr]::Zero
function Save-DesktopDiagnostic {
  try {
    $snapshot = Join-Path (Split-Path $DiagnosticsPath) (([IO.Path]::GetFileNameWithoutExtension($DiagnosticsPath)) + '-desktop.png')
    & (Join-Path $PSScriptRoot 'windows-dialog-desktop.ps1') -ApplicationPid $ApplicationPid -ReportPath ($snapshot + '.json') -ImagePath $snapshot
    $diagnostic.desktop_diagnostic = [IO.Path]::GetFileName($snapshot)
  } catch { $diagnostic.desktop_diagnostic_error_type = $_.Exception.GetType().FullName }
}
try {
  Add-Type @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
public static class MisakaDialogInput {
  [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom; }
  [StructLayout(LayoutKind.Sequential)] public struct Point { public int x, y; }
  [StructLayout(LayoutKind.Sequential)] public struct MouseInput { public int dx, dy; public uint data, flags, time; public UIntPtr extra; }
  [StructLayout(LayoutKind.Explicit)] public struct InputData { [FieldOffset(0)] public MouseInput mouse; }
  [StructLayout(LayoutKind.Sequential)] public struct Input { public uint type; public InputData data; }
  [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool IsChild(IntPtr parent, IntPtr child);
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr hwnd, uint command);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hwnd, StringBuilder value, int maximum);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr hwnd, StringBuilder value, int maximum);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(Point point);
  [DllImport("user32.dll", SetLastError=true)] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll", SetLastError=true)] public static extern uint SendInput(uint count, Input[] input, int size);
  private static string Text(IntPtr hwnd, bool className) {
    var text=new StringBuilder(161);
    if(className) GetClassName(hwnd,text,text.Capacity); else GetWindowText(hwnd,text,text.Capacity);
    return text.ToString();
  }
  public static int[] Click(long dialogValue, long buttonValue, uint expectedPid, string expectedCaption) {
    var dialog=new IntPtr(dialogValue); var button=new IntPtr(buttonValue); var owner=GetWindow(dialog,4);
    uint dialogPid,buttonPid,ownerPid;
    GetWindowThreadProcessId(dialog,out dialogPid); GetWindowThreadProcessId(button,out buttonPid); GetWindowThreadProcessId(owner,out ownerPid);
    if(dialogPid!=expectedPid || buttonPid!=expectedPid || ownerPid!=expectedPid || owner==IntPtr.Zero ||
      Text(dialog,true)!="#32770" || (Text(dialog,false)!="Select a screen" && Text(dialog,false)!="选择屏幕") || Text(button,true)!="Button" ||
      Text(button,false)!=expectedCaption || !IsChild(dialog,button) || !IsWindowVisible(dialog) ||
      !IsWindowEnabled(dialog) || !IsWindowVisible(button) || !IsWindowEnabled(button))
      throw new InvalidOperationException("The actual native source control changed before input.");
    Rect parentRect,buttonRect;
    if(!GetWindowRect(dialog,out parentRect) || !GetWindowRect(button,out buttonRect) ||
      buttonRect.Right-buttonRect.Left<2 || buttonRect.Bottom-buttonRect.Top<2 ||
      buttonRect.Left<parentRect.Left || buttonRect.Right>parentRect.Right ||
      buttonRect.Top<parentRect.Top || buttonRect.Bottom>parentRect.Bottom)
      throw new InvalidOperationException("The source button is not inside the actual dialog.");
    int x=buttonRect.Left+(buttonRect.Right-buttonRect.Left)/2, y=buttonRect.Top+(buttonRect.Bottom-buttonRect.Top)/2;
    SetForegroundWindow(dialog);
    if(!SetCursorPos(x,y)) throw new Win32Exception(Marshal.GetLastWin32Error());
    var hit=WindowFromPoint(new Point { x=x,y=y });
    if(hit!=button && !IsChild(button,hit)) throw new InvalidOperationException("The verified source button is obscured at the real cursor position.");
    var events=new[] { new Input { type=0, data=new InputData { mouse=new MouseInput { flags=2 } } },
      new Input { type=0, data=new InputData { mouse=new MouseInput { flags=4 } } } };
    uint sent=SendInput(2,events,Marshal.SizeOf(typeof(Input)));
    if(sent!=2) throw new Win32Exception(Marshal.GetLastWin32Error(),"Native mouse input was incomplete.");
    return new[] { x,y,(int)sent };
  }
}
'@
  $previousContext = [MisakaDialogInput]::SetThreadDpiAwarenessContext([IntPtr](-4))
  if ($previousContext -eq [IntPtr]::Zero) { throw 'Native input requires physical per-monitor DPI coordinates.' }
  Save-DesktopDiagnostic
  $deadline = (Get-Date).AddSeconds($DeadlineSeconds)
  do {
    # UIA RootElement omitted a proven visible Windows TaskDialog in the actual
    # runner. Enumerate real HWNDs instead, then click its real source button.
    $all = @([MisakaDialogDesktop]::Snapshot([uint32]$ApplicationPid))
    $dialogs = @($all | Where-Object { -not $_.child -and $_.pid -eq $ApplicationPid -and $_.visible -and $_.enabled -and $_.class_name -eq '#32770' -and $_.title -in @('Select a screen','选择屏幕') })
    foreach ($window in $dialogs) {
      $owner = @($all | Where-Object { $_.hwnd -eq $window.owner -and $_.pid -eq $ApplicationPid })
      if ($owner.Count -ne 1) { continue }
      $buttons = @($all | Where-Object { $_.child -and $_.pid -eq $ApplicationPid -and $_.class_name -eq 'Button' -and $_.visible -and $_.enabled -and [MisakaDialogInput]::IsChild([IntPtr]$window.hwnd,[IntPtr]$_.hwnd) })
      if (@($buttons | Where-Object { $_.title -in @('Cancel','取消') }).Count -ne 1) { continue }
      $source = @($buttons | Where-Object { $_.title -match '^(Entire screen|Screen(?:\s|\d).*|Display(?:\s|\d).*|屏幕 [1-9]\d*)$' })
      if ($source.Count -lt 1) { continue }
      $button = $source[0]
      Save-DesktopDiagnostic
      $nativeClick = [MisakaDialogInput]::Click($window.hwnd,$button.hwnd,[uint32]$ApplicationPid,$button.title)
      $diagnostic.windows = @($window)
      $diagnostic.selected_button = $button.title
      $diagnostic.selected_hwnd = $button.hwnd
      $diagnostic.input = [ordered]@{ method = 'SetCursorPos + SendInput'; x = $nativeClick[0]; y = $nativeClick[1]; events_sent = $nativeClick[2] }
      $diagnostic.invoked = $true
      exit 0
    }
    Start-Sleep -Milliseconds 200
  } while ((Get-Date) -lt $deadline)
  throw 'No verified source button was found in the actual native selection dialog.'
} catch {
  $diagnostic.error_type = $_.Exception.GetType().FullName
  $diagnostic.error = $_.Exception.Message
  throw
} finally {
  if (-not $diagnostic.invoked) { Save-DesktopDiagnostic }
  if ($previousContext -ne [IntPtr]::Zero) { [void][MisakaDialogInput]::SetThreadDpiAwarenessContext($previousContext) }
  $diagnostic | ConvertTo-Json -Depth 7 | Set-Content -Encoding utf8 $DiagnosticsPath
}
