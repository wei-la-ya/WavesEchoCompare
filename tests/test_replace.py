import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from wse_core.parser import EchoEntry
from wse_core.replace import ReplaceError, compare_phantoms

from gsuid_core.plugins.XutheringWavesUID.XutheringWavesUID.utils.api.model import RoleDetailData


def _entry(cost: int, value: int) -> EchoEntry:
    return EchoEntry(cost, "攻击", {1: "18%", 3: "30%", 4: "33%"}[cost], [("攻击", str(value))])


def _values(role: RoleDetailData) -> list[float]:
    assert role.phantomData is not None and role.phantomData.equipPhantomList is not None
    return [
        float(slot.subProps[0].attributeValue)
        for slot in role.phantomData.equipPhantomList
        if slot is not None and slot.subProps
    ]


@pytest.mark.parametrize(("cost", "value", "slot"), [(3, 100, 1), (3, 1, 1), (1, 100, 3), (1, 1, 3)])
def test_selects_highest_damage_even_when_all_choices_lose(
    role_detail: RoleDetailData, cost: int, value: int, slot: int
) -> None:
    calls: list[float] = []

    def score(role: RoleDetailData) -> float:
        total = sum(_values(role))
        calls.append(total)
        return total

    original = role_detail.model_dump_json()
    result = asyncio.run(compare_phantoms(role_detail, [_entry(cost, value)], score))
    assert result.slot_indices == (slot,)
    assert result.before == 135
    assert result.damage_sum == 135 - _values(role_detail)[slot] + value
    assert len(calls) == result.candidate_count + 1 == 3
    assert role_detail.model_dump_json() == original


def test_joint_selection_avoids_greedy_local_optimum(role_detail: RoleDetailData) -> None:
    def score(role: RoleDetailData) -> float:
        pair = tuple(_values(role)[1:3])
        return {(40, 60): 50, (1, 60): 100, (40, 1): 90, (1, 2): 10, (2, 1): 200}[pair]

    result = asyncio.run(compare_phantoms(role_detail, [_entry(3, 1), _entry(3, 2)], score))
    assert result.slot_indices == (2, 1)
    assert result.damage_sum == 200
    assert result.candidate_count == 2


def test_multiple_costs_replace_distinct_slots_and_keep_both_mains(role_detail: RoleDetailData) -> None:
    def score(role: RoleDetailData) -> float:
        return sum(_values(role))

    entries = [_entry(4, 9), _entry(3, 8), _entry(3, 7), _entry(1, 6), _entry(1, 5)]
    result = asyncio.run(compare_phantoms(role_detail, entries, score))
    assert len(set(result.slot_indices)) == 5
    assert result.candidate_count == 4
    assert result.role_detail.phantomData is not None
    slots = result.role_detail.phantomData.equipPhantomList
    assert slots is not None
    for entry, index in zip(entries, result.slot_indices, strict=True):
        slot = slots[index]
        assert slot is not None and slot.mainProps is not None
        assert len(slot.mainProps) == 2
        assert slot.mainProps[1].attributeValue == {1: "2280", 3: "100", 4: "150"}[entry.cost]
    assert result.role_detail.equipPhantomAddPropList is not None


def test_rejects_excess_same_cost_before_computing(role_detail: RoleDetailData) -> None:
    def unexpected_score(role: RoleDetailData) -> float:
        raise AssertionError("无效输入不应计算")

    with pytest.raises(ReplaceError, match="仅有 1 个"):
        asyncio.run(compare_phantoms(role_detail, [_entry(4, 1), _entry(4, 2)], unexpected_score))


def test_uses_actual_44111_layout(role_detail: RoleDetailData) -> None:
    assert role_detail.phantomData is not None and role_detail.phantomData.equipPhantomList is not None
    for slot, cost in zip(role_detail.phantomData.equipPhantomList, [4, 4, 1, 1, 1], strict=True):
        assert slot is not None
        slot.cost = cost

    def score(role: RoleDetailData) -> float:
        return sum(_values(role))

    entries = [_entry(4, 1), _entry(4, 2), _entry(1, 3), _entry(1, 4), _entry(1, 5)]
    result = asyncio.run(compare_phantoms(role_detail, entries, score))
    assert result.candidate_count == 12
    assert len(set(result.slot_indices)) == 5
    assert result.damage_sum == 15


def test_parallel_scoring_does_not_block_event_loop(role_detail: RoleDetailData) -> None:
    barrier = threading.Barrier(3)
    release = threading.Event()

    def score(role: RoleDetailData) -> float:
        barrier.wait(timeout=3)
        assert release.wait(timeout=3), "计算阻塞了事件循环"
        return sum(_values(role))

    async def run() -> None:
        with ThreadPoolExecutor(max_workers=3) as pool:
            task = asyncio.create_task(compare_phantoms(role_detail, [_entry(3, 1)], score, pool))
            await asyncio.sleep(0.02)
            release.set()
            assert (await task).slot_indices == (1,)

    asyncio.run(run())
