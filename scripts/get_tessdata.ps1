# Downloads the OCR language data (Georgian, English, Russian; about 35 MB, Apache-2.0).
$target = Join-Path (Split-Path $PSScriptRoot) 'data\tessdata'
[IO.Directory]::CreateDirectory($target) | Out-Null
# A fixed version, checked: native code in the program reads these files.
$version = 'e12c65a915945e4c28e237a9b52bc4a8f39a0cec'
$known = [ordered]@{
    kat = '557f48b753fdbbee2c77d83bae2542af249f915be6cf80883892a579ea2a81db'
    eng = '8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba'
    rus = 'b617eb6830ffabaaa795dd87ea7fd251adfe9cf0efe05eb9a2e8128b7728d6b6'
}
function Test-Sum($path, $sum) {
    (Test-Path -LiteralPath $path) -and ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -eq $sum)
}
$ProgressPreference = 'SilentlyContinue'  # Windows PowerShell downloads many times faster without it
foreach ($lang in $known.Keys) {
    $file = Join-Path $target "$lang.traineddata"
    if (Test-Sum $file $known[$lang]) { "$lang - already here"; continue }
    $url = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/$version/$lang.traineddata"
    $part = [IO.Path]::GetTempFileName()  # -OutFile cannot write to a folder with [ ] in its name
    try {
        Invoke-WebRequest -Uri $url -OutFile $part -UseBasicParsing -ErrorAction Stop
        if (-not (Test-Sum $part $known[$lang])) {
            throw "$lang - the downloaded file is not the expected one"
        }
        [IO.File]::Copy($part, $file, $true)
    } finally {
        Remove-Item -LiteralPath $part -Confirm:$false
    }
    "$lang - downloaded"
}
