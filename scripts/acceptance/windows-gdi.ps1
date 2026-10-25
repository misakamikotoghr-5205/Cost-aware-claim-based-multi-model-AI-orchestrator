param([Parameter(Mandatory=$true)][string]$ReportPath,
      [Parameter(Mandatory=$true)][string]$ImagePath)
$ErrorActionPreference = 'Stop'
$report = [ordered]@{ diagnostic_fixture = $true; product_ui = $false; method = 'System.Drawing.CopyFromScreen'; platform = 'Windows' }
$bitmap = $null
$graphics = $null
$previousContext = [IntPtr]::Zero
try {
  Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class MisakaGdiProbeDpi {
  [DllImport("user32.dll", SetLastError=true)]
  public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
}
'@
  $previousContext = [MisakaGdiProbeDpi]::SetThreadDpiAwarenessContext([IntPtr](-4))
  if ($previousContext -eq [IntPtr]::Zero) { throw 'Could not establish physical per-monitor DPI coordinates.' }
  Add-Type -AssemblyName System.Drawing
  Add-Type -AssemblyName System.Windows.Forms
  $bounds = [Windows.Forms.Screen]::PrimaryScreen.Bounds
  if ($bounds.Width -le 0 -or $bounds.Height -le 0 -or [long]$bounds.Width * $bounds.Height -gt 16777216) {
    throw 'Diagnostic screen size is unavailable or exceeds its limit.'
  }
  $report.bounds = [ordered]@{ x = $bounds.X; y = $bounds.Y; width = $bounds.Width; height = $bounds.Height }
  $started = Get-Date
  $bitmap = [Drawing.Bitmap]::new($bounds.Width, $bounds.Height)
  $graphics = [Drawing.Graphics]::FromImage($bitmap)
  $graphics.CopyFromScreen($bounds.X, $bounds.Y, 0, 0, $bounds.Size, [Drawing.CopyPixelOperation]::SourceCopy)
  $bitmap.Save($ImagePath, [Drawing.Imaging.ImageFormat]::Png)
  $colors = @{}
  for ($x = 0; $x -lt $bounds.Width; $x += [Math]::Max(1, [int]($bounds.Width / 20))) {
    for ($y = 0; $y -lt $bounds.Height; $y += [Math]::Max(1, [int]($bounds.Height / 20))) {
      $colors[$bitmap.GetPixel($x, $y).ToArgb()] = $true
    }
  }
  $report.elapsed_ms = [int]((Get-Date) - $started).TotalMilliseconds
  $report.sampled_pixel_variants = $colors.Count
  $report.png_bytes = (Get-Item $ImagePath).Length
  $report.image_sha256 = (Get-FileHash $ImagePath -Algorithm SHA256).Hash.ToLowerInvariant()
  $report.result = if ($colors.Count -gt 1) { 'captured_nonuniform_screen' } else { 'captured_uniform_screen' }
} catch {
  $report.result = 'unavailable'
  $report.error_type = $_.Exception.GetType().FullName
  $report.error = $_.Exception.Message
} finally {
  if ($null -ne $graphics) { $graphics.Dispose() }
  if ($null -ne $bitmap) { $bitmap.Dispose() }
  if ($previousContext -ne [IntPtr]::Zero) { [void][MisakaGdiProbeDpi]::SetThreadDpiAwarenessContext($previousContext) }
  $report.finished_at = (Get-Date).ToUniversalTime().ToString('o')
  $report | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 $ReportPath
}
