# WavesEchoCompare 插件设计文档

> 依赖 XutheringWavesUID（下称 XW）。功能：用户发送声骸截图 + `ww角色名提升[cost]`，
> 插件识别截图声骸词条 → 替换到绑定 UID 面板对应 cost 槽位 → 渲染替换后面板图 →
> 对比替换前后「全部伤害类型求和」，回复「伤害提升了约 XXX」/「伤害降低了约 XXX」。

## 1. 调研结论（XW 事实源，均已核实源码）

| 能力 | 位置 | 说明 |
|------|------|------|
| 绑定 uid | `XW/utils/database/models.py` `WavesBind.get_uid_by_game(user_id, bot_id)` | 与 XW 一致 |
| 面板数据 | `XW/utils/char_info_utils.py` `get_char_detail_for_id(uid, char_id)` | 读本地 rawData.json |
| 角色名解析 | `alias_to_char_name` / `char_name_to_char_id`（`utils/name_convert.py`） | 支持别名 |
| 替换声骸主词条 | `XW/wutheringwaves_charinfo/role_info_change.py` `change_role_detail()` | 支持 `换声骸主词条c1攻击` 等 change_list 语法 |
| 主词条满值表 | 同上 `phantom_main_value` | c1 攻击18% 生命22.8% 防御18%；c3 属性伤30% 攻击30% 生命30% 防御38% 共鸣效率32%；c4 暴击22% 爆伤44% 治疗效果加成26.4% |
| 渲染替换后面板 | `XW/wutheringwaves_charinfo/draw_char_card.py` `draw_char_detail_img(..., change_list_regex=...)` | 返回 bytes 或 str |
| 伤害计算 | `calc.damageAttribute = calc.card_sort_map_to_attribute(calc.role_card)`，再 `damage_temp["func"](damageAttributeTemp, role_detail)` 返回 `(暴击伤害, 期望伤害)` | `DamageDetailRegister.find_class(char_id)` 取列表 |
| 图片提取 | `ev.content` 中 type 为 `img`/`image` 的 http URL（`card_utils.get_image` 同款逻辑） | 自实现，不 import XW 内部私有函数 |
| XW 前缀 | `Plugins(name="XutheringWavesUID", force_prefix=["ww"])` | 本插件 `Plugins(force_prefix=["ww"])` 对齐 |

### 1.1 声骸词条结构 (XW rawData 实证 + 官方规则)

cost1/3/4 满级 (25 级) 声骸均为**双主词条 + 5 副词条**:

| cost | 主词条 1 (随机) | 主词条 2 (固定) | 副词条 |
|---|---|---|---|
| 4 | 暴击 22% / 暴击伤害 44% / 治疗效果加成 26.4% / 攻击 33% / 生命 33% / 防御 41.8% | 攻击 150 | 5 条 |
| 3 | 属性伤害加成 30% / 攻击 30% / 生命 30% / 防御 38% / 共鸣效率 32% | 攻击 100 | 5 条 |
| 1 | 攻击 18% / 生命 22.8% / 防御 18% | 生命 2280 | 5 条 |

- cost1/cost3 主词条 1 **绝无**暴击/暴击伤害。
- 固定主词条数值恒定 (满级), 用于截图识别时剥离: 属性+数值精确匹配, 数值抖动时按满值容错。
- 固定主词条来源: [9game 声骸属性大全](https://www.9game.cn/mingchao/9856075.html)
  「所有声骸的主属性有两条」+ XW rawData 实证 (cost4=[暴击伤害44%,攻击150], cost3=[属性伤30%,攻击100], cost1=[攻击18%,生命2280])。
- `属性伤害加成` 按 role_detail.role.attributeName 具体化为「X伤害加成」。

## 2. 命令

```
ww<角色名>提升[cost]
```

- 例：`ww长离提升3`、`ww长离提升`（无 cost，若截图也无法判断 cost 则提示补发）。
- 触发器：`on_regex(r"^(?P<char>{PATTERN})提升(?P<cost>[1-4])?$", block=True, priority=4)`。
- 必须附声骸截图同条消息发送；无图时提示。
- PATTERN 沿用 XW 的 `utils/char_info_utils.py` 同款正则（自拷贝，不 import XW 内部模块）。

## 3. 识别（本地 OCR，rapidocr 懒加载）

- 依赖 `rapidocr-onnxruntime`（pyproject 声明，启动时自动装）。
- 流程：`httpx` 下载截图 → `to_thread` 里跑 OCR → 解析文本行。
- **双 pass 抗模糊**：原图 + 2x LANCZOS 放大各跑一次，按 Y 聚簇合并（每簇取文本更长行）。
  实测（模拟图）：清晰/轻度模糊 5/5 副词条；拍屏摩尔纹 5/5；严重模糊（半分辨率+blur2.5）
  无法识别 → 明确报错要求重拍或命令带 cost。
- **错字映射**：实测高频 OCR 错字（量击→暴击、玫击→攻击等）先修正再走属性归一。
- 属性名归一：`暴伤/爆伤`→暴击伤害、`充能/共效`→共鸣效率、`攻击%`→攻击 等。
- **partial 标记**：副词条识别 <4 条时结果标记 partial，回复文案附加「截图较模糊,
  结果仅供参考」提醒。
- 副词条数值保留 `%` 单位（与 XW subProps 格式一致：'10.5%' / '60'）。

## 4. 替换与对比

1. 取绑定 uid + 面板 `role_detail`（deepcopy 两份：基线 & 替换）。
2. cost3 → 替换面板 equipPhantomList 中第一个 `cost==3` 的槽位；cost1 → 第一个 `cost==1` 槽位；
   cost4 同理。若该面板无对应 cost 槽位 → 报错。
3. 替换实现：改写该槽 `mainProps[0].attributeName/attributeValue`（满值表），
   并按截图重写 `subProps`（数值原样）。套装/声骸本体不变（用户已确认默认同套装，
   结果文案注明「默认截图声骸与原声骸同套装，套装效果未变」）。
4. 槽位选择（cost3/cost1 各 2 个槽位时）：**两个槽位都试，取伤害更高者**作为替换结果。
5. 伤害口径：**全部伤害类型求和**——遍历 `DamageDetailRegister.find_class(char_id)`
   每个 `func(attr, role_detail)` 的 `expected_damage`，去千分位后 float 求和。
   - 基线 = 原面板求和 S0；替换 = 替换后面板求和 S1；Δ = S1 - S0。
   - `Δ > 0`：`伤害提升了约 XXX`；`Δ < 0`：`伤害降低了约 XXX`（保留两位小数，千分位）。
   - 无 damageDetail 模块 → 回退综合评分对比（XW ScoreDetailRegister），文案注明是评分。
6. 渲染：复用 `draw_char_detail_img(ev, uid, char, user_id, change_list_regex=None)` 无法注入
   自定义声骸 → 本插件**不重复造轮子渲染**，而是构造替换后的 `RoleDetailData` 后调用
   XW `draw_char_detail_img` 的 `role_detail_override` 参数（源码已核实该参数存在，
   `get_role_need` 中 `role_detail_override is not None` 时直接返回 override）。

## 5. 目录结构

```
gsuid_core/plugins/WavesEchoCompare/
├── __init__.py / __nest__.py          ← 外层入口 + 嵌套标记
├── pyproject.toml                     ← 依赖 rapidocr-onnxruntime / httpx / pillow
├── ruff.toml / pyrightconfig.json / .vscode/ / README.md
└── WavesEchoCompare/
    ├── __init__.py                    ← Plugins(name=..., force_prefix=["ww"])
    ├── __full__.py
    ├── wse_trigger/                   ← 触发器
    │   └── __init__.py
    └── wse_core/
        ├── __init__.py
        ├── ocr.py                     ← rapidocr 懒加载 + to_thread 识别
        ├── parser.py                  ← OCR 行 → 声骸词条结构 + cost 白名单校验
        ├── replace.py                 ← 构造替换后 RoleDetailData + 双槽位择优
        └── damage.py                  ← 全伤害求和 + 对比文案
```

`Plugins(force_prefix=["ww"])` 与 XW 同前缀，但插件名不同 → 命令字面 `ww角色名提升`
只有本插件的 on_regex 会命中（XW 无「提升」命令，已核实其注册列表无冲突）。

## 6. 红线遵循

- 全 async；OCR/图像走 `to_thread`。
- 无 try/except 兜底（rapidocr import 失败属「软依赖缺失」→ 模块顶层探测 + 显式提示，
  不吞业务异常）；无 cast / type:ignore / getattr 兜底 / Any。
- `#` 注释 ≤2 行、≤88 字符。
- OCR 结果属「不可信外部输入」，`parser.py` 内允许对 OCR 文本清洗做白名单校验式守卫
  （isinstance/正则），不做 try/except 吞错。
