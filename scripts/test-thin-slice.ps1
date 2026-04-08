param(
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"

function Assert-Equal {
    param(
        [string]$Label,
        $Actual,
        $Expected
    )

    if ($Actual -eq $Expected) {
        Write-Host "[OK] $Label -> $Actual" -ForegroundColor Green
    } else {
        Write-Host "[FEHLER] $Label -> erwartet: $Expected | erhalten: $Actual" -ForegroundColor Red
        exit 1
    }
}

function Invoke-JsonPost {
    param(
        [string]$Uri,
        [hashtable]$Body
    )

    return Invoke-RestMethod -Method Post -Uri $Uri -ContentType "application/json" -Body ($Body | ConvertTo-Json)
}

try {
    Invoke-RestMethod -Method Get -Uri "$BaseUrl/system/health" | Out-Null
} catch {
    Write-Host "[FEHLER] Server nicht erreichbar unter $BaseUrl" -ForegroundColor Red
    Write-Host "Starte zuerst den FastAPI-Server, z. B. mit:" -ForegroundColor Yellow
    Write-Host 'python -m uvicorn goal_ops_console.main:app --reload'
    exit 1
}

$goal = Invoke-JsonPost -Uri "$BaseUrl/goals" -Body @{
    title = "Launch website"
    description = "Thin-slice test"
    urgency = 0.9
    value = 0.8
    deadline_score = 0.4
}

$goalId = $goal.goal_id
Assert-Equal "Goal nach Erstellung" $goal.state "draft"

$goalActivated = Invoke-RestMethod -Method Post -Uri "$BaseUrl/goals/$goalId/activate"
Assert-Equal "Goal nach Aktivierung" $goalActivated.state "active"

$task = Invoke-JsonPost -Uri "$BaseUrl/tasks" -Body @{
    goal_id = $goalId
    title = "Prepare landing page"
}

$taskId = $task.task_id
Assert-Equal "Task nach Erstellung" $task.status "pending"

$failBody = @{
    failure_type = "SkillFailure"
    error_message = "Repeated skill failure"
}

$fail1 = Invoke-JsonPost -Uri "$BaseUrl/tasks/$taskId/fail" -Body $failBody
Assert-Equal "Task nach 1. SkillFailure" $fail1.status "failed"
Assert-Equal "Retry Count nach 1. SkillFailure" $fail1.retry_count 1

$fail2 = Invoke-JsonPost -Uri "$BaseUrl/tasks/$taskId/fail" -Body $failBody
Assert-Equal "Task nach 2. SkillFailure" $fail2.status "poison"
Assert-Equal "Retry Count nach 2. SkillFailure" $fail2.retry_count 2

$goalAfterFail = Invoke-RestMethod -Method Get -Uri "$BaseUrl/goals/$goalId"
Assert-Equal "Goal nach 2x SkillFailure" $goalAfterFail.state "escalation_pending"

$approved = Invoke-RestMethod -Method Post -Uri "$BaseUrl/goals/$goalId/hitl_approve"
Assert-Equal "Goal nach HITL Approve" $approved.state "active"

$finalGoal = Invoke-RestMethod -Method Get -Uri "$BaseUrl/goals/$goalId"
$finalTask = Invoke-RestMethod -Method Get -Uri "$BaseUrl/tasks/$taskId"
$trace = Invoke-RestMethod -Method Get -Uri "$BaseUrl/events?correlation_id=$goalId"

if ($trace.Count -ge 8) {
    Write-Host "[OK] Event-Trace vorhanden -> $($trace.Count) Events" -ForegroundColor Green
} else {
    Write-Host "[FEHLER] Event-Trace zu kurz -> nur $($trace.Count) Events" -ForegroundColor Red
    exit 1
}

$eventTypes = $trace.event_type
if ($eventTypes -contains "goal.created" -and
    $eventTypes -contains "goal.activated" -and
    $eventTypes -contains "task.created" -and
    $eventTypes -contains "task.failed" -and
    $eventTypes -contains "task.poison.detected" -and
    $eventTypes -contains "goal.escalation_pending" -and
    $eventTypes -contains "goal.hitl_approved") {
    Write-Host "[OK] Wichtige Events im Trace gefunden" -ForegroundColor Green
} else {
    Write-Host "[FEHLER] Nicht alle erwarteten Events wurden gefunden" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Thin Slice erfolgreich getestet." -ForegroundColor Cyan
Write-Host "Goal ID: $goalId"
Write-Host "Task ID: $taskId"

Write-Host ""
Write-Host "Kurz-Zusammenfassung" -ForegroundColor Cyan
[PSCustomObject]@{
    GoalId = $goalId
    FinalGoalState = $finalGoal.state
    FinalQueueStatus = $finalGoal.queue_status
    TaskId = $taskId
    FinalTaskState = $finalTask.status
    RetryCount = $finalTask.retry_count
    FinalCorrelation = $finalTask.correlation_id
    EventCount = $trace.Count
} | Format-Table -AutoSize

Write-Host ""
Write-Host "Formatierter Event-Trace" -ForegroundColor Cyan
$trace |
    Select-Object seq, event_type, entity_id, correlation_id, emitted_at |
    Format-Table -AutoSize

Write-Host ""
Write-Host "Event-Payloads" -ForegroundColor Cyan
foreach ($event in $trace) {
    Write-Host ("seq=" + $event.seq + " | " + $event.event_type + " | " + $event.correlation_id) -ForegroundColor Yellow
    $event.payload | ConvertTo-Json -Depth 8
    Write-Host ""
}
