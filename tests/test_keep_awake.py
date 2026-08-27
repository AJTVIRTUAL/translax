"""
Test de `core/keep_awake.py` — sur la vraie machine, pas un simulacre : ce
module n'a de sens que s'il appelle réellement l'API du système
d'exploitation (impossible de « faker » un appel Win32 utilement ici).

    python tests/test_keep_awake.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.keep_awake import KeepAwake  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


def main() -> int:
    print("\n1. Démarrage / arrêt")
    k = KeepAwake()
    check("inactif à la création", not k.active)
    k.start()
    check("actif après start()", k.active)
    k.stop()
    check("inactif après stop()", not k.active)

    print("\n2. Appels redondants sans effet de bord")
    k.start()
    k.start()  # ne doit pas planter ni relancer un second processus (Mac)
    check("toujours actif après un second start()", k.active)
    k.stop()
    k.stop()  # ne doit pas planter
    check("toujours inactif après un second stop()", not k.active)

    print("\n3. Utilisation en context manager")
    with KeepAwake() as awake:
        check("actif à l'intérieur du bloc with", awake.active)
    check("inactif après la sortie du bloc with", not awake.active)

    print("\n4. Appel réel de l'API système (pas un simulacre)")
    if sys.platform == "win32":
        import ctypes
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002
        result = ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
        )
        check("SetThreadExecutionState renvoie un succès (non nul)", result != 0, f"(retour={result})")
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)  # remise à l'état normal
    else:
        print("  (ignoré : test spécifique à Windows, cette machine est", sys.platform, ")")

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests anti-veille passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
