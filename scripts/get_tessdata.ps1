# Downloads the OCR language data (Georgian, English, Russian; about 35 MB, Apache-2.0).
$target = Join-Path (Split-Path $PSScriptRoot) 'data\tessdata'
New-Item -ItemType Directory -Force $target | Out-Null
foreach ($lang in 'kat', 'eng', 'rus') {
    $file = Join-Path $target "$lang.traineddata"
    if (Test-Path $file) { "$lang - already here"; continue }
    $url = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/$lang.traineddata"
    Invoke-WebRequest -Uri $url -OutFile "$file.part" -ErrorAction Stop
    Move-Item "$file.part" $file
    "$lang - downloaded"
}
