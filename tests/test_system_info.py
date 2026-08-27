"""
Tests de `core/system_info.py` -- diagnostic matériel réel pour la page
Paramètres (demande explicite de l'utilisateur, 25/08/2026) : vérifie sur
CETTE machine (pas un simulacre) que la détection reflète bien le matériel
réel déjà confirmé par ailleurs dans ce projet -- AMD Ryzen 7 4700U,
8 cœurs, aucun GPU dédié (voir SPEC.md).

    python tests/test_system_info.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import system_info  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


def main() -> int:
    print("\n1. Détection réelle sur cette machine (jamais un simulacre)")
    info = system_info.detect()

    check("un nom de système est renseigné", bool(info.os_name), f"({info.os_name})")
    check("un nom de CPU est renseigné (jamais vide/None)", bool(info.cpu_name), f"({info.cpu_name})")
    check("le nombre de cœurs est cohérent (au moins 1)", info.cpu_cores >= 1, f"({info.cpu_cores})")
    check("PyTorch est bien détecté comme installé sur cette machine", info.torch_available)

    # Machine de référence de ce projet, déjà confirmée par ailleurs
    # (voir SPEC.md) : AMD Ryzen 7 4700U, aucun GPU dédié. Un vrai test
    # d'intégration, pas une supposition -- si ce test tourne un jour sur
    # une autre machine, ce check précis (mais pas les autres) échouera à
    # juste titre et devra être adapté.
    check("cette machine précise est bien reconnue sans GPU CUDA (AMD Ryzen 7 4700U, référence du projet)",
          not info.gpu_available and info.gpu_name is None, f"(gpu_available={info.gpu_available})")
    check("le device pour Précis/OPUS-MT/MADLAD-400 est bien 'cpu' en l'absence de GPU",
          info.device_for_gpu_capable_engines == "cpu")
    check("« Ryzen » apparaît dans le nom de CPU détecté sur cette machine",
          "ryzen" in info.cpu_name.lower(), f"({info.cpu_name!r})")

    print("\n2. Ne plante jamais, même si torch était absent (simulé)")
    import builtins
    real_import = builtins.__import__

    def blocking_import(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("simulé pour ce test")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = blocking_import
    try:
        info_no_torch = system_info.detect()
    finally:
        builtins.__import__ = real_import

    check("torch_available=False quand l'import échoue", not info_no_torch.torch_available)
    check("gpu_available=False de façon sûre quand torch est absent", not info_no_torch.gpu_available)
    check("une note explique l'absence de PyTorch, rien de caché",
          any("PyTorch" in note for note in info_no_torch.detection_notes), f"({info_no_torch.detection_notes})")
    check("cpu_name reste renseigné même sans torch (détection indépendante)", bool(info_no_torch.cpu_name))

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests de diagnostic matériel passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
