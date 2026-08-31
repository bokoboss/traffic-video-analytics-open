$SupportedNodeMinimum = [version]"20.19.0"
$SupportedNodeMaximumExclusive = [version]"27.0.0"
$RequiredPnpmVersion = "11.9.0"

function ConvertTo-TvaVersion($VersionText) {
    if ($VersionText -match "v?(\d+)\.(\d+)\.(\d+)") {
        return [version]"$($Matches[1]).$($Matches[2]).$($Matches[3])"
    }
    return $null
}

function Test-TvaNodeVersion($VersionText) {
    $parsed = ConvertTo-TvaVersion $VersionText
    return ($null -ne $parsed -and $parsed -ge $SupportedNodeMinimum -and $parsed -lt $SupportedNodeMaximumExclusive)
}

function Invoke-TvaNodeVersion($NodeExe) {
    try {
        $version = & $NodeExe --version 2>$null | Select-Object -First 1
    } catch {
        throw "node --version failed for $NodeExe"
    }
    if ([string]::IsNullOrWhiteSpace($version)) {
        throw "node --version returned no output for $NodeExe"
    }
    if (!(Test-TvaNodeVersion $version)) {
        throw "Unsupported Node.js version $version. Supported range is >= $SupportedNodeMinimum and < $SupportedNodeMaximumExclusive."
    }
    return $version.Trim()
}

function Resolve-TvaNodeFromDirectory($Directory, $Source, $SourceLabel) {
    $nodeExe = Join-Path $Directory "node.exe"
    if (!(Test-Path -LiteralPath $nodeExe -PathType Leaf)) {
        throw "Node executable not found at $nodeExe"
    }
    $resolvedNode = (Resolve-Path -LiteralPath $nodeExe).Path
    $version = Invoke-TvaNodeVersion $resolvedNode
    return [pscustomobject]@{
        Source = $Source
        SourceLabel = $SourceLabel
        Directory = Split-Path -Parent $resolvedNode
        Executable = $resolvedNode
        Version = $version
    }
}

function Resolve-TvaPnpmManagedNode {
    $pnpm = Get-Command "pnpm" -ErrorAction SilentlyContinue
    if ($null -eq $pnpm) {
        return $null
    }
    $pnpmPath = (Resolve-Path -LiteralPath $pnpm.Source).Path
    $candidate = Join-Path (Split-Path -Parent $pnpmPath) "..\..\node\bin\node.exe"
    $resolved = Resolve-Path -LiteralPath $candidate -ErrorAction SilentlyContinue
    if ($null -eq $resolved) {
        return $null
    }
    $pnpmLower = $pnpmPath.ToLowerInvariant()
    $nodeLower = $resolved.Path.ToLowerInvariant()
    if (!$pnpmLower.Contains("\dependencies\bin\fallback\")) {
        return $null
    }
    if (!$nodeLower.EndsWith("\dependencies\node\bin\node.exe")) {
        return $null
    }
    return $resolved.Path
}

function Resolve-TvaNodeRuntime($Root) {
    if (![string]::IsNullOrWhiteSpace($env:TVA_NODE_DIR)) {
        if (!(Test-Path -LiteralPath $env:TVA_NODE_DIR -PathType Container)) {
            throw "TVA_NODE_DIR does not exist: $env:TVA_NODE_DIR"
        }
        return Resolve-TvaNodeFromDirectory $env:TVA_NODE_DIR "configured" "node_configured_path"
    }

    $localNode = Join-Path $Root ".local-tools\node"
    if (Test-Path -LiteralPath $localNode -PathType Container) {
        return Resolve-TvaNodeFromDirectory $localNode "repository-local" "node_repository_local"
    }

    $pathNode = Get-Command "node" -ErrorAction SilentlyContinue
    if ($null -ne $pathNode) {
        $nodeExe = (Resolve-Path -LiteralPath $pathNode.Source).Path
        $version = Invoke-TvaNodeVersion $nodeExe
        return [pscustomobject]@{
            Source = "system PATH"
            SourceLabel = "node_system_path"
            Directory = Split-Path -Parent $nodeExe
            Executable = $nodeExe
            Version = $version
        }
    }

    $fallbackNode = Resolve-TvaPnpmManagedNode
    if ($null -ne $fallbackNode) {
        $version = Invoke-TvaNodeVersion $fallbackNode
        return [pscustomobject]@{
            Source = "pnpm runtime fallback"
            SourceLabel = "node_pnpm_fallback"
            Directory = Split-Path -Parent $fallbackNode
            Executable = $fallbackNode
            Version = $version
        }
    }

    throw "No approved Node.js runtime was found. Set TVA_NODE_DIR to an IT-approved portable Node directory, place Node under .local-tools\node, or ask IT to install Node.js on PATH."
}

function Get-TvaChildPath($NodeRuntime, $PnpmCommand) {
    $parts = @($NodeRuntime.Directory)
    if ($null -ne $PnpmCommand -and $null -ne $PnpmCommand.Executable) {
        $parts += (Split-Path -Parent $PnpmCommand.Executable)
    }
    $parts += $env:PATH
    return ($parts -join [IO.Path]::PathSeparator)
}

function Invoke-TvaPnpmVersion($Executable) {
    try {
        $version = & $Executable --version 2>$null | Select-Object -First 1
    } catch {
        return $null
    }
    if ([string]::IsNullOrWhiteSpace($version)) {
        return $null
    }
    return $version.Trim()
}

function Resolve-TvaPnpmCommand($Root, $NodeRuntime) {
    if (![string]::IsNullOrWhiteSpace($env:TVA_PNPM_CMD)) {
        if (!(Test-Path -LiteralPath $env:TVA_PNPM_CMD -PathType Leaf)) {
            throw "TVA_PNPM_CMD does not exist: $env:TVA_PNPM_CMD"
        }
        $resolved = (Resolve-Path -LiteralPath $env:TVA_PNPM_CMD).Path
        $version = Invoke-TvaPnpmVersion $resolved
        if ($version -eq $RequiredPnpmVersion) {
            return [pscustomobject]@{
                Source = "configured"
                Executable = $resolved
                Version = $version
                RequiredVersion = $RequiredPnpmVersion
            }
        }
        if ($null -eq $version) {
            throw "pnpm --version failed for configured command $resolved."
        }
        throw "pnpm version $version was found at $resolved, but $RequiredPnpmVersion is required."
    }

    $localPnpm = Join-Path $Root ".local-tools\pnpm\pnpm.cmd"
    if (Test-Path -LiteralPath $localPnpm -PathType Leaf) {
        $resolved = (Resolve-Path -LiteralPath $localPnpm).Path
        $version = Invoke-TvaPnpmVersion $resolved
        if ($version -eq $RequiredPnpmVersion) {
            return [pscustomobject]@{
                Source = "repository-local"
                Executable = $resolved
                Version = $version
                RequiredVersion = $RequiredPnpmVersion
            }
        }
        if ($null -eq $version) {
            throw "pnpm --version failed for repository-local command $resolved."
        }
        throw "pnpm version $version was found at $resolved, but $RequiredPnpmVersion is required."
    }

    $candidates = @()
    $previousPath = $env:PATH
    try {
        $env:PATH = "$($NodeRuntime.Directory)$([IO.Path]::PathSeparator)$previousPath"
        $pathPnpm = Get-Command "pnpm" -ErrorAction SilentlyContinue
        if ($null -ne $pathPnpm) {
            $candidates += [pscustomobject]@{ Source = "PATH"; Path = $pathPnpm.Source }
        }
    } finally {
        $env:PATH = $previousPath
    }

    foreach ($candidate in $candidates) {
        if (!(Test-Path -LiteralPath $candidate.Path -PathType Leaf)) {
            continue
        }
        $resolved = (Resolve-Path -LiteralPath $candidate.Path).Path
        $version = Invoke-TvaPnpmVersion $resolved
        if ($version -eq $RequiredPnpmVersion) {
            return [pscustomobject]@{
                Source = $candidate.Source
                Executable = $resolved
                Version = $version
                RequiredVersion = $RequiredPnpmVersion
            }
        }
        if ($null -ne $version) {
            throw "pnpm version $version was found at $resolved, but $RequiredPnpmVersion is required."
        }
    }

    throw "pnpm $RequiredPnpmVersion is unavailable. Prepare a local pnpm command under .local-tools\pnpm\pnpm.cmd, configure TVA_PNPM_CMD, install pnpm through IT, or explicitly run setup with an approved Corepack preparation workflow."
}

function Write-TvaPortableNodeInstructions {
    Write-Host ""
    Write-Host "Portable Node.js preparation:"
    Write-Host "1. Ask IT to approve a Windows x64 Node.js ZIP in the supported range >= $SupportedNodeMinimum and < $SupportedNodeMaximumExclusive."
    Write-Host "2. Extract it outside Git tracking, for example:"
    Write-Host "   .local-tools\node\node.exe"
    Write-Host "   .local-tools\node\npm.cmd"
    Write-Host "   .local-tools\node\npx.cmd"
    Write-Host "3. Do not commit .local-tools, archives, or runtime binaries."
    Write-Host "4. Rerun setup_app.bat. The launcher modifies PATH only for child processes."
}
