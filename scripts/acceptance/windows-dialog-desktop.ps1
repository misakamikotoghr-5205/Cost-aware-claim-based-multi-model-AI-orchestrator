param([Parameter(Mandatory=$true)][int]$ApplicationPid,
      [Parameter(Mandatory=$true)][string]$ReportPath,
      [Parameter(Mandatory=$true)][string]$ImagePath)
$ErrorActionPreference = 'Stop'
$report = [ordered]@{ diagnostic_only = $true; product_capture_api = $false; expected_pid = $ApplicationPid; windows = @() }
$bitmap = $null
$graphics = $null
$previousContext = [IntPtr]::Zero
try {
  # Observe this empty CI desktop. Never create a fixture over the actual dialog,
  # and never substitute diagnostic pixels for the product's own native crop.
  if (-not ('MisakaDialogDesktop' -as [type])) { Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class MisakaDialogDesktop {
  public delegate bool EnumProc(IntPtr hwnd, IntPtr parameter);
  [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom; }
  public sealed class Window { public long hwnd, owner, parent; public uint pid; public bool visible, enabled, child; public string title, class_name; public Rect bounds; }
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc callback, IntPtr parameter);
  [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr hwnd, EnumProc callback, IntPtr parameter);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr hwnd, uint command);
  [DllImport("user32.dll")] public static extern IntPtr GetParent(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hwnd, StringBuilder value, int maximum);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr hwnd, StringBuilder value, int maximum);
  [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
  private static Window Read(IntPtr hwnd, bool child) {
    uint pid; GetWindowThreadProcessId(hwnd, out pid);
    var title=new StringBuilder(161); GetWindowText(hwnd,title,title.Capacity);
    var name=new StringBuilder(81); GetClassName(hwnd,name,name.Capacity);
    Rect rect; GetWindowRect(hwnd,out rect);
    return new Window { hwnd=hwnd.ToInt64(), owner=GetWindow(hwnd,4).ToInt64(), parent=GetParent(hwnd).ToInt64(), pid=pid,
      visible=IsWindowVisible(hwnd), enabled=IsWindowEnabled(hwnd), child=child, title=title.ToString(), class_name=name.ToString(), bounds=rect };
  }
  public static Window[] Snapshot(uint expected) {
    var list=new List<Window>(); var roots=new List<Window>();
    EnumWindows((hwnd,p)=>{ if(roots.Count>=256) return false; roots.Add(Read(hwnd,false)); return true; },IntPtr.Zero);
    // Retain every top-level HWND, including hidden and foreign-process windows.
    list.AddRange(roots);
    foreach(var root in roots) {
      if(root.pid!=expected && root.class_name!="#32770") continue;
      int children=0;
      EnumChildWindows(new IntPtr(root.hwnd),(hwnd,p)=>{ if(children++>=128) return false; list.Add(Read(hwnd,true)); return true; },IntPtr.Zero);
    }
    return list.ToArray();
  }
}
'@
  }
  $report.windows = @([MisakaDialogDesktop]::Snapshot([uint32]$ApplicationPid))
  $previousContext = [MisakaDialogDesktop]::SetThreadDpiAwarenessContext([IntPtr](-4))
  if ($previousContext -eq [IntPtr]::Zero) { throw 'Physical DPI context was not available.' }
  Add-Type -AssemblyName System.Drawing
  Add-Type -AssemblyName System.Windows.Forms
  $bounds = [Windows.Forms.Screen]::PrimaryScreen.Bounds
  if ($bounds.Width -le 0 -or $bounds.Height -le 0 -or [long]$bounds.Width*$bounds.Height -gt 16777216) { throw 'Desktop diagnostic exceeds its size limit.' }
  $bitmap = [Drawing.Bitmap]::new($bounds.Width,$bounds.Height)
  $graphics = [Drawing.Graphics]::FromImage($bitmap)
  $graphics.CopyFromScreen($bounds.X,$bounds.Y,0,0,$bounds.Size,[Drawing.CopyPixelOperation]::SourceCopy)
  $bitmap.Save($ImagePath,[Drawing.Imaging.ImageFormat]::Png)
  $report.result = 'current_desktop_observed'
  $report.image_sha256 = (Get-FileHash $ImagePath -Algorithm SHA256).Hash.ToLowerInvariant()
} catch {
  $report.result = 'diagnostic_unavailable'
  $report.error_type = $_.Exception.GetType().FullName
} finally {
  if ($null -ne $graphics) { $graphics.Dispose() }
  if ($null -ne $bitmap) { $bitmap.Dispose() }
  if ($previousContext -ne [IntPtr]::Zero) { [void][MisakaDialogDesktop]::SetThreadDpiAwarenessContext($previousContext) }
  $report.finished_at = (Get-Date).ToUniversalTime().ToString('o')
  $report | ConvertTo-Json -Depth 7 | Set-Content -Encoding utf8 $ReportPath
}
