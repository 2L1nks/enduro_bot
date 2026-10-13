"""Подбор мотоцикла под участника.

Задача: раздать N свободных мотоциклов M участникам так, чтобы каждому
досталась подходящая техника. Жадный перебор «первому попавшемуся —
первый подходящий» здесь работает плохо: рослый участник может забрать
единственный универсальный мотоцикл, и невысокому не останется ничего,
хотя решение существовало.

Поэтому идём от самых ограниченных: участник, которому подходит меньше
всего техники, выбирает первым, и получает наименее универсальный
из подходящих мотоциклов — универсальные оставляем тем, кто идёт следом.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.db.models import Experience, Motorcycle

#: Насколько участник может «перерасти» свою категорию.
#: Новичку опытный мотоцикл не даём, опытному новичковый — можно.
_EXPERIENCE_RANK = {
    Experience.BEGINNER: 0,
    Experience.INTERMEDIATE: 1,
    Experience.ADVANCED: 2,
}


@dataclass(slots=True)
class RiderProfile:
    """Параметры участника, по которым подбирается техника."""

    name: str
    height_cm: int | None = None
    weight_kg: int | None = None
    experience: Experience | None = None


def fits(moto: Motorcycle, rider: RiderProfile) -> bool:
    """Подходит ли мотоцикл участнику.

    Незаданные параметры не ограничивают: если у мотоцикла не указан
    предел по весу, он подходит любому. Так прокат может завести технику
    быстро, не заполняя всю карточку сразу.
    """
    if rider.height_cm is not None:
        if moto.min_rider_height_cm and rider.height_cm < moto.min_rider_height_cm:
            return False
        if moto.max_rider_height_cm and rider.height_cm > moto.max_rider_height_cm:
            return False

    if rider.weight_kg is not None and moto.max_rider_weight_kg:
        if rider.weight_kg > moto.max_rider_weight_kg:
            return False

    if rider.experience is not None:
        # Техника для опытных недоступна тем, кто ниже уровнем
        if _EXPERIENCE_RANK[moto.category] > _EXPERIENCE_RANK[rider.experience]:
            return False

    return True


def suitable_for(motorcycles: list[Motorcycle], rider: RiderProfile) -> list[Motorcycle]:
    return [m for m in motorcycles if fits(m, rider)]


def assign(
    motorcycles: list[Motorcycle], riders: list[RiderProfile]
) -> tuple[dict[str, Motorcycle], list[RiderProfile]]:
    """Распределяет технику по участникам.

    Возвращает пару: словарь «имя участника -> мотоцикл» и список тех,
    кому подходящей техники не нашлось.

    Порядок разбора — от самых ограниченных участников к самым
    непривередливым. Внутри выбора берём мотоцикл, который подходит
    наименьшему числу остальных участников: универсальную технику
    бережём для тех, у кого выбор уже.
    """
    free = list(motorcycles)
    assigned: dict[str, Motorcycle] = {}
    unassigned: list[RiderProfile] = []

    # Сколько вариантов у каждого участника сейчас
    pending = sorted(riders, key=lambda r: len(suitable_for(free, r)))

    for rider in pending:
        options = suitable_for(free, rider)
        if not options:
            unassigned.append(rider)
            continue

        others = [r for r in pending if r is not rider and r.name not in assigned]
        # Чем меньше людей подходит этому мотоциклу, тем он «уже»
        # специализирован — такой и отдаём, универсальный прибережём.
        best = min(options, key=lambda m: sum(fits(m, o) for o in others))

        assigned[rider.name] = best
        free.remove(best)

    return assigned, unassigned
