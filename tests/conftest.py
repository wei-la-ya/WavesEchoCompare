import pytest

from gsuid_core.plugins.XutheringWavesUID.XutheringWavesUID.utils.api.model import (
    Role,
    Props,
    Weapon,
    WeaponData,
    PhantomProp,
    EquipPhantom,
    FetterDetail,
    RoleDetailData,
    EquipPhantomData,
)


@pytest.fixture
def role_detail() -> RoleDetailData:
    slots = [
        EquipPhantom(
            phantomProp=PhantomProp(
                phantomPropId=i, name=f"声骸{i}", phantomId=i, quality=5, cost=cost, iconUrl="", skillDescription=None
            ),
            cost=cost,
            quality=5,
            level=25,
            fetterDetail=FetterDetail(groupId=1),
            mainProps=[],
            subProps=[Props(attributeName="攻击", attributeValue=str(value))],
        )
        for i, (cost, value) in enumerate(zip([4, 3, 3, 1, 1], [5, 40, 60, 10, 20], strict=True))
    ]
    return RoleDetailData(
        role=Role(
            roleId=1202, level=90, roleName="测试角色", starLevel=5, attributeId=1, attributeName="热熔", weaponTypeId=1
        ),
        level=90,
        chainList=[],
        skillList=[],
        weaponData=WeaponData(
            weapon=Weapon(
                weaponId=1,
                weaponName="测试武器",
                weaponType=1,
                weaponStarLevel=5,
                weaponIcon=None,
                weaponEffectName=None,
            ),
            level=90,
            resonLevel=1,
        ),
        phantomData=EquipPhantomData(cost=12, equipPhantomList=slots),
    )
