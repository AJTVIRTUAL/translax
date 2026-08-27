"""
Test de `core/heartbeat.py`.

    python tests/test_heartbeat.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.heartbeat import Heartbeat  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


def main() -> int:
    print("\n1. État initial")
    hb = Heartbeat()
    check("aucun pas au départ", hb.ticks == 0)
    check("pas de blocage juste après la création", hb.seconds_since_beat() < 1.0)

    print("\n2. beat() avance l'horodatage et le compteur")
    time.sleep(0.05)
    before = hb.seconds_since_beat()
    hb.beat()
    after = hb.seconds_since_beat()
    check("seconds_since_beat() retombe près de zéro après beat()", after < before)
    check("un pas comptabilisé", hb.ticks == 1)
    hb.beat()
    hb.beat()
    check("les pas s'accumulent", hb.ticks == 3)

    print("\n3. seconds_since_beat() augmente sans nouveau beat()")
    hb.beat()
    time.sleep(0.1)
    check("le temps écoulé grandit tant que rien ne bat", hb.seconds_since_beat() >= 0.1)

    print("\n4. reset()")
    hb.reset()
    check("compteur remis à zéro", hb.ticks == 0)
    check("horodatage remis à maintenant", hb.seconds_since_beat() < 1.0)

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests de pouls passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
