# Windows notifications for the agents VM: a toast for every event that needs you (a task reported done or
# needs-you, an agent asking for permission, Claude Code that ended by itself). Follows ~/agents/events.jsonl
# on the VM over ssh and reconnects when the connection drops. Start it with agents-notify.cmd; close the
# window to stop. The watcher on the VM (agents.sh watcher start) writes the events.
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
# The VM's ssh host alias and the repo folder on it (docs/ssh-setup.md); set AGENTS_VM_HOST or AGENTS_VM_DIR to change them.
$vmHost = if ($env:AGENTS_VM_HOST) { $env:AGENTS_VM_HOST } else { 'agents' }
$vmDir = if ($env:AGENTS_VM_DIR) { $env:AGENTS_VM_DIR } else { '~/claude-agents-vm' }
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'

function Show-Toast([string]$title, [string]$text) {
    $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $nodes = $xml.GetElementsByTagName('text')
    $nodes.Item(0).AppendChild($xml.CreateTextNode($title)) | Out-Null
    $nodes.Item(1).AppendChild($xml.CreateTextNode($text)) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
}

if ($args -contains '-test') {
    Show-Toast 'agents' 'Notifications from the agents VM work.'
    exit 0
}

Write-Host "Following the agents' events; a notification appears for each one that needs you. Close this window to stop."
while ($true) {
    ssh -o BatchMode=yes -o ServerAliveInterval=30 $vmHost "$vmDir/mcp/events --follow" | ForEach-Object {
        try { $e = $_ | ConvertFrom-Json } catch { return }
        $task = if ($e.task) { "$($e.task) " } else { '' }
        $agent = if ($e.agent) { " ($($e.agent))" } else { '' }
        $title = "$task$($e.kind -replace '-', ' ')$agent"
        # The event's own time is the VM's clock (UTC); show it in this machine's time zone.
        $when = [DateTimeOffset]::FromUnixTimeSeconds([long]$e.at).LocalDateTime.ToString('HH:mm')
        Write-Host "$when  $title  $($e.text)"
        if ($e.notify) { Show-Toast $title $e.text }
    }
    Write-Host 'Connection lost; reconnecting in 10 s...'
    Start-Sleep 10
}
