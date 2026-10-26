# Writes a spoken test sentence as a 16 kHz mono 16-bit WAV, using Windows' built-in speech
# synthesizer, for: python -m talk_to_mic --transcribe <file>
# Usage: .\scripts\make-sample-wav.ps1 [-Path sample.wav] [-Text "..."]

param(
    [string]$Path = "sample.wav",
    [string]$Text = "Um, so I think we should, uh, meet on Tuesday. No wait, I meant Wednesday."
)

$ErrorActionPreference = "Stop"
$Path = [System.IO.Path]::GetFullPath($Path, $PWD.Path)
New-Item -ItemType Directory -Force (Split-Path $Path) | Out-Null
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
try {
    $synth.SetOutputToWaveFile($Path, $format)
    $synth.Speak($Text)
} finally {
    $synth.Dispose()
}
Write-Host "Wrote $Path"
