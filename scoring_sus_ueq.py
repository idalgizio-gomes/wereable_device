"""Calcula scores SUS e UEQ-S a partir de USABILIDADE_RESPOSTAS_TEMPLATE.csv.

Uso: python scoring_sus_ueq.py respostas.csv
"""

import csv
import statistics
import sys

SUS_ODD = [1, 3, 5, 7, 9]
SUS_EVEN = [2, 4, 6, 8, 10]
UEQ_PRAGMATIC = [1, 2, 3, 4]
UEQ_HEDONIC = [5, 6, 7, 8]


def sus_score(row: dict) -> float | None:
    try:
        total = 0
        for i in SUS_ODD:
            total += int(row[f"SUS{i}"]) - 1
        for i in SUS_EVEN:
            total += 5 - int(row[f"SUS{i}"])
        return total * 2.5
    except (ValueError, KeyError):
        return None


def ueq_scores(row: dict) -> tuple[float, float] | tuple[None, None]:
    try:
        pragmatic = statistics.mean(int(row[f"UEQ{i}"]) for i in UEQ_PRAGMATIC)
        hedonic = statistics.mean(int(row[f"UEQ{i}"]) for i in UEQ_HEDONIC)
        # escala 1-7 -> -3..+3
        return pragmatic - 4, hedonic - 4
    except (ValueError, KeyError):
        return None, None


def main() -> None:
    if len(sys.argv) != 2:
        print("Uso: python scoring_sus_ueq.py respostas.csv")
        sys.exit(1)

    with open(sys.argv[1], newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    by_perfil: dict[str, list[float]] = {}
    for row in rows:
        sus = sus_score(row)
        if sus is None:
            continue
        prag, hed = ueq_scores(row)
        perfil = row.get("perfil", "?")
        by_perfil.setdefault(perfil, []).append(sus)
        print(f"{row.get('participant_id','?')} ({perfil}): SUS={sus:.1f}  "
              f"UEQ pragmatica={prag:.2f}  UEQ hedonica={hed:.2f}" if prag is not None
              else f"{row.get('participant_id','?')} ({perfil}): SUS={sus:.1f}  UEQ=incompleto")

    print("\n=== Por perfil ===")
    for perfil, scores in by_perfil.items():
        print(f"{perfil}: n={len(scores)}  SUS medio={statistics.mean(scores):.1f}  "
              f"min={min(scores):.1f}  max={max(scores):.1f}")


if __name__ == "__main__":
    main()
