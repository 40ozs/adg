#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Configuration and share-filtering rules.

    These are the decisions that determine what a scan looks at, so they are also the
    decisions that determine what an audit can miss. A filter bug does not produce an
    error; it produces a smaller report that still looks complete.
#>

BeforeAll {
    $moduleRoot = Split-Path -Parent $PSScriptRoot
    Import-Module (Join-Path $moduleRoot 'AdgSmbCollector.psd1') -Force
    $script:TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "adg-smb-config-$([guid]::NewGuid())"
    New-Item -ItemType Directory -Path $script:TempRoot -Force | Out-Null
}

AfterAll {
    if ($script:TempRoot -and (Test-Path -LiteralPath $script:TempRoot)) {
        Remove-Item -LiteralPath $script:TempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    Remove-Module AdgSmbCollector -Force -ErrorAction SilentlyContinue
}

Describe 'Import-AdgSmbTarget' {

    Context 'targets are always explicit' {

        It 'refuses to run without a target rather than defaulting to the domain' {
            { Import-AdgSmbTarget } | Should -Throw '*No target servers were configured*'
        }

        It 'accepts servers passed directly' {
            $settings = Import-AdgSmbTarget -Server 'FS01', 'FS02'
            $settings.Servers | Should -Be @('FS01', 'FS02')
        }

        It 'merges file and parameter targets, keeping each host once' {
            $path = Join-Path $script:TempRoot 'merge.json'
            '{ "servers": ["FS01", "FS02"] }' | Set-Content -LiteralPath $path -Encoding utf8

            $settings = Import-AdgSmbTarget -Path $path -Server 'FS02', 'FS03'

            # FS02 appears in both; scanning it twice in one run would produce duplicate
            # source keys within a batch, which the contract rejects outright.
            $settings.Servers | Should -Be @('FS01', 'FS02', 'FS03')
        }

        It 'treats host names case-insensitively when deduplicating' {
            $settings = Import-AdgSmbTarget -Server 'FS01', 'fs01'
            $settings.Servers.Count | Should -Be 1
        }

        It 'rejects a UNC path where a host name belongs' {
            { Import-AdgSmbTarget -Server '\\FS01\Finance' } | Should -Throw '*is not a host name*'
        }
    }

    Context 'validation happens before any session is opened' {

        It 'reports a missing configuration file by name' {
            $missing = Join-Path $script:TempRoot 'not-here.json'
            { Import-AdgSmbTarget -Path $missing } | Should -Throw "*$missing*"
        }

        It 'reports malformed JSON as malformed JSON' {
            $path = Join-Path $script:TempRoot 'broken.json'
            '{ "servers": [' | Set-Content -LiteralPath $path -Encoding utf8

            { Import-AdgSmbTarget -Path $path } | Should -Throw '*is not valid JSON*'
        }

        It 'rejects a batch size above the contract maximum' {
            $path = Join-Path $script:TempRoot 'batch.json'
            '{ "servers": ["FS01"], "batchSize": 5000 }' | Set-Content -LiteralPath $path -Encoding utf8

            { Import-AdgSmbTarget -Path $path } | Should -Throw '*batchSize*'
        }

        It 'rejects an unknown protocol' {
            $path = Join-Path $script:TempRoot 'protocol.json'
            '{ "servers": ["FS01"], "protocol": "Telnet" }' | Set-Content -LiteralPath $path -Encoding utf8

            { Import-AdgSmbTarget -Path $path } | Should -Throw '*protocol*'
        }

        It 'rejects an unknown ACL method' {
            $path = Join-Path $script:TempRoot 'acl.json'
            '{ "servers": ["FS01"], "aclMethod": "Guess" }' | Set-Content -LiteralPath $path -Encoding utf8

            { Import-AdgSmbTarget -Path $path } | Should -Throw '*aclMethod*'
        }
    }

    Context 'defaults' {

        It 'excludes administrative and hidden shares, and records excluded shares' {
            $settings = Import-AdgSmbTarget -Server 'FS01'

            $settings.IncludeAdminShares | Should -BeFalse
            $settings.IncludeHiddenShares | Should -BeFalse
            $settings.IncludeNonDiskShares | Should -BeFalse
            # On by default: it is what lets a run claim it enumerated the whole server,
            # which is what makes a deleted share detectable.
            $settings.RecordExcludedShares | Should -BeTrue
            $settings.AclMethod | Should -Be 'Auto'
        }

        It 'reads the shipped example configuration' {
            $example = Join-Path (Split-Path -Parent $PSScriptRoot) 'adg-smb-targets.example.json'
            $settings = Import-AdgSmbTarget -Path $example

            $settings.Servers.Count | Should -BeGreaterThan 0
            $settings.Exclude | Should -Contain 'Temp*'
        }

        It 'carries no credential field, because the collector stores no secret' {
            $example = Join-Path (Split-Path -Parent $PSScriptRoot) 'adg-smb-targets.example.json'
            $raw = Get-Content -LiteralPath $example -Raw

            $raw | Should -Not -Match '(?i)"?(password|secret|credential)"?\s*:'
        }
    }
}

Describe 'Test-AdgShareIncluded' {

    Context 'administrative and hidden shares' {

        It 'excludes an administrative share by default' {
            $verdict = Test-AdgShareIncluded -ShareName 'C$' -IsSpecial $true
            $verdict.Included | Should -BeFalse
            $verdict.Reason | Should -Be 'administrative or system share'
        }

        It 'includes an administrative share when asked' {
            (Test-AdgShareIncluded -ShareName 'C$' -IsSpecial $true -IncludeAdminShares).Included | Should -BeTrue
        }

        It 'treats an ordinary hidden share as hidden, not administrative' {
            # Data$ is an administrator's decision to publish data unlisted; C$ is not.
            # Conflating them either audits Windows's own shares or misses real data.
            $verdict = Test-AdgShareIncluded -ShareName 'Data$' -IsSpecial $false
            $verdict.Reason | Should -Be 'hidden share'

            (Test-AdgShareIncluded -ShareName 'Data$' -IsSpecial $false -IncludeHiddenShares).Included | Should -BeTrue
        }

        It 'believes the server Special flag over the shape of the name' {
            # A share named like an ordinary one that the server marks Special is Special.
            $verdict = Test-AdgShareIncluded -ShareName 'Finance' -IsSpecial $true
            $verdict.Reason | Should -Be 'administrative or system share'
        }

        It 'falls back to the name test when the server did not say' {
            $verdict = Test-AdgShareIncluded -ShareName 'ADMIN$'
            $verdict.Included | Should -BeFalse
        }

        It 'includes an ordinary share' {
            (Test-AdgShareIncluded -ShareName 'Finance' -IsSpecial $false).Included | Should -BeTrue
        }
    }

    Context 'pattern precedence' {

        It 'lets an explicit exclude beat an explicit include' {
            $verdict = Test-AdgShareIncluded -ShareName 'TempData' -Include 'Temp*' -Exclude 'TempData'
            $verdict.Included | Should -BeFalse
            $verdict.Reason | Should -Be "excluded by pattern 'TempData'"
        }

        It 'excludes anything unmatched once includes are configured' {
            $verdict = Test-AdgShareIncluded -ShareName 'Finance' -Include 'Projects*'
            $verdict.Included | Should -BeFalse
            $verdict.Reason | Should -Be 'no include pattern matched'
        }

        It 'lets an include override the admin-share default, since it was asked for by name' {
            (Test-AdgShareIncluded -ShareName 'C$' -IsSpecial $true -Include 'C$').Included | Should -BeTrue
        }

        It 'matches patterns case-insensitively' {
            (Test-AdgShareIncluded -ShareName 'FINANCE' -Include 'finance').Included | Should -BeTrue
        }

        It 'ignores a blank pattern rather than excluding everything' {
            (Test-AdgShareIncluded -ShareName 'Finance' -Exclude @('', '   ')).Included | Should -BeTrue
        }
    }

    Context 'share types' {

        It 'excludes a share that publishes no directory' {
            $verdict = Test-AdgShareIncluded -ShareName 'Printer' -ShareType 'print' -IsSpecial $false
            $verdict.Included | Should -BeFalse
            $verdict.Reason | Should -Match 'publishes no directory'
        }

        It 'includes non-disk shares when asked' {
            (Test-AdgShareIncluded -ShareName 'Printer' -ShareType 'print' -IsSpecial $false -IncludeNonDiskShares).Included |
                Should -BeTrue
        }
    }
}

Describe 'Test-AdgHiddenShareName' {

    It 'reads hidden-ness from the name, which is where it lives' {
        Test-AdgHiddenShareName 'Data$' | Should -BeTrue
        Test-AdgHiddenShareName 'Finance' | Should -BeFalse
    }
}
