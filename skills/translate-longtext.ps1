param(
    [Parameter(Mandatory = $true)]
    [string]$InputFile,

    [int]$MaxChars = 5000,

    [string]$LangCode = "fr-fr"
)

# --- Vérifications ---
if (!(Test-Path $InputFile)) {
    Write-Error "Fichier introuvable: $InputFile"
    exit 1
}

# =========================
# ENCODAGE (CRITIQUE)
# =========================
$Utf8      = [System.Text.UTF8Encoding]::new($false) # sans BOM
$Utf8Bom   = [System.Text.UTF8Encoding]::new($true)  # avec BOM

$OutputEncoding = $Utf8
[Console]::InputEncoding  = $Utf8
[Console]::OutputEncoding = $Utf8



# =========================
# RÉSOLUTION DES CHEMINS
# =========================
#$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Si le chemin est relatif → le rendre absolu (par rapport au dossier courant)
#$InputFile = Resolve-Path -Path $InputFile -ErrorAction Stop

# Dossier de travail = dossier du script
#Set-Location $ScriptDir

$baseName = [System.IO.Path]::GetFileNameWithoutExtension($InputFile)
$outputFinal = "${baseName}_fr.txt"

$morceauxDir = "morceaux"
$resultsDir  = "results"

New-Item -ItemType Directory -Force -Path $morceauxDir | Out-Null
New-Item -ItemType Directory -Force -Path $resultsDir  | Out-Null

# --- Lecture du texte ---
$text = Get-Content $InputFile -Raw

# Séparation en paragraphes (ligne vide)
$paragraphes = $text -split "(\r?\n){2,}"

$morceaux = @()
$current = ""

function Add-Morceau {
    param($content)
    if ($content.Trim().Length -gt 0) {
        $script:morceaux += $content.Trim()
    }
}

foreach ($p in $paragraphes) {

    if (($current.Length + $p.Length) -le $MaxChars) {
        $current += $p + "`n`n"
        continue
    }

    # Si le paragraphe est trop gros → découpe par phrases
    if ($p.Length -gt $MaxChars) {
        Add-Morceau $current
        $current = ""

        $phrases = $p -split "(?<=[\.!\?])\s+"
        foreach ($phrase in $phrases) {
            if (($current.Length + $phrase.Length) -le $MaxChars) {
                $current += $phrase + " "
            } else {
                Add-Morceau $current
                $current = $phrase + " "
            }
        }
        $current += "`n`n"
    }
    else {
        Add-Morceau $current
        $current = $p + "`n`n"
    }
}

Add-Morceau $current

# --- Écriture des morceaux ---
$morceauxFiles = @()
for ($i = 0; $i -lt $morceaux.Count; $i++) {
    $num = ($i + 1).ToString("000")
    $file = Join-Path $morceauxDir "morceau_$num.txt"
    #$morceaux[$i] | Set-Content -Encoding UTF8 $file
    $morceaux[$i] | Set-Content $file
    $morceauxFiles += $file
}

Write-Host "Découpage terminé: $($morceauxFiles.Count) morceaux"

# --- Traduction ---
for ($i = 0; $i -lt $morceauxFiles.Count; $i++) {

    $input  = $morceauxFiles[$i]
    $num    = ($i + 1).ToString("000")
    $output = Join-Path $resultsDir "output_$num.txt"

    Write-Progress `
        -Activity "Traduction en cours" `
        -Status "Morceau $($i + 1) / $($morceauxFiles.Count)" `
        -PercentComplete ((($i + 1) / $morceauxFiles.Count) * 100)

    # Injection du contenu via stdin PowerShell
    Get-Content $input -Raw |
        & fabric.exe `
            -o $output `
            -p translate `
            -v "lang_code:$LangCode"
}

Write-Progress -Activity "Traduction en cours" -Completed

# --- Fusion finale ---
Get-ChildItem $resultsDir -Filter "output_*.txt" |
    Sort-Object Name |
    Get-Content |
    Set-Content  $outputFinal

Write-Host " Traduction complète : $outputFinal"
