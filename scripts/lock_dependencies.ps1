Param(
    [string]$PythonExe = "python"
)

# Generate deterministic lock file from requirements.in
# Requires pip-tools: pip install pip-tools

$ErrorActionPreference = "Stop"

Write-Host "[Lock] Installing pip-tools..."
& $PythonExe -m pip install --upgrade pip pip-tools

Write-Host "[Lock] Compiling requirements.in -> requirements.lock.txt"
& $PythonExe -m piptools compile requirements.in --output-file requirements.lock.txt

Write-Host "[Lock] Done"
