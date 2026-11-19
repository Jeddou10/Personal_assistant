$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    python -m assistant_app
} finally {
    Pop-Location
}
