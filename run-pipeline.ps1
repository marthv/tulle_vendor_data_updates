<#
.SYNOPSIS
    Run an extraction command with ANTHROPIC_API_KEY scoped to that run only.

.DESCRIPTION
    Claude Code resolves ANTHROPIC_API_KEY ahead of the subscription login. Setting the
    key in a shell and later launching `claude` from that same shell silently bills those
    sessions to the metered API instead of the flat subscription — that accounted for
    roughly $600-1,000 of the July 2026 API bill.

    This wrapper sets the key, runs the command, and restores the previous value in a
    finally block, so the key never lingers in an interactive session.

    Store the key under ANTHROPIC_PIPELINE_KEY, not ANTHROPIC_API_KEY. Claude Code does
    not read that name, so it is safe to set permanently:

        [Environment]::SetEnvironmentVariable(
            'ANTHROPIC_PIPELINE_KEY', '<key>', 'User')

    If ANTHROPIC_PIPELINE_KEY is unset, the script prompts (input is not echoed).

.EXAMPLE
    .\run-pipeline.ps1 python launch_parallel.py --ids-file pe_todo.txt

.EXAMPLE
    .\run-pipeline.ps1 python run_chunk.py --ids "P1,P2" --batch

.EXAMPLE
    .\run-pipeline.ps1 python ingest_batch.py msgbatch_abc --wait 60
#>
# NOTE: deliberately no param()/[CmdletBinding()] block. With CmdletBinding, PowerShell
# tries to bind pass-through flags like `-c` as parameters of THIS script and fails with
# "A positional parameter cannot be found". The automatic $args collects everything
# verbatim, which is what a wrapper needs.
$Command = $args

if (-not $Command -or $Command.Count -eq 0) {
    Write-Host "usage: .\run-pipeline.ps1 <command> [args...]"
    Write-Host "  e.g. .\run-pipeline.ps1 python launch_parallel.py --ids-file pe_todo.txt"
    exit 1
}

# Warn loudly if the caller already has the key exported — that is the leak this
# script exists to prevent, and it means `claude` launched from here would bill the API.
if ($env:ANTHROPIC_API_KEY) {
    Write-Warning ("ANTHROPIC_API_KEY is already set in this shell. Do NOT launch " +
                   "Claude Code from here - it would bill the API instead of the " +
                   "subscription. Run 'Remove-Item Env:ANTHROPIC_API_KEY' when done.")
}

$key = $env:ANTHROPIC_PIPELINE_KEY
if (-not $key) {
    $secure = Read-Host -Prompt "Anthropic API key for this pipeline run" -AsSecureString
    if ($secure -and $secure.Length -gt 0) {
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
    }
}
if (-not $key) {
    Write-Error "No key supplied. Set ANTHROPIC_PIPELINE_KEY or enter it when prompted."
    exit 1
}

$exe = $Command[0]
# Guard the slice: $Command[1..0] would return elements in REVERSE order, not empty.
$rest = if ($Command.Count -gt 1) { $Command[1..($Command.Count - 1)] } else { @() }

$hadPrevious = Test-Path Env:ANTHROPIC_API_KEY
$previous = if ($hadPrevious) { $env:ANTHROPIC_API_KEY } else { $null }
$code = 1

try {
    $env:ANTHROPIC_API_KEY = $key
    & $exe @rest
    $code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
}
finally {
    # Always restore, including on Ctrl-C or a mid-run throw, so the key does not
    # outlive the command that needed it.
    if ($hadPrevious) {
        $env:ANTHROPIC_API_KEY = $previous
    } else {
        Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
    }
}

exit $code
