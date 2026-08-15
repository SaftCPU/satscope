# Wird von umbreld GESOURCT, nicht ausgefuehrt: kein Shebang, kein exit.
# Laeuft unter "set -euo pipefail" - jede nicht gesetzte Variable wuerde den
# gesamten App-Start abbrechen, deshalb ueberall Vorgabewerte.
export APP_SATSCOPE_PORT="${APP_SATSCOPE_PORT:-4020}"
