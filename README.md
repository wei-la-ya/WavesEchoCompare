# WavesEchoCompare

鸣潮声骸截图对比插件（依赖 [XutheringWavesUID](../XutheringWavesUID)）。

## 用法

发送声骸截图，并附命令（同一条消息）：

```
ww<角色名>提升<cost>       例: ww长离提升3
ww<角色名>声骸比一比        例: ww长离声骸比一比 (cost 由截图识别)
```

截图内无法识别 COST 且命令未带 cost → 提示补发。

> 注意: `ww长离提升`（不带数字）是 XutheringWavesUID 的优化建议命令, 不属于本插件。

## 功能

1. 识别截图声骸的 cost、主词条、副词条（本地 OCR, 双 pass 抗模糊）。
2. 取当前绑定 UID 内对应角色面板, 替换对应 cost 槽位声骸词条。
3. cost3/cost1 有两个槽位时, 两个槽位都试, 取伤害更高的方案。
4. 对比替换前后「全部伤害类型求和」, 回复: `伤害提升了约 X.X%` / `伤害降低了约 X.X%`。
5. 回复附带左右拼接面板图 (左: 替换前 / 右: 替换后)。

## 依赖安装

插件 `pyproject.toml` 声明了 `rapidocr-onnxruntime`, gsuid_core 启动时自动安装
(需开启 `AutoInstallDep` 配置)。也可手动安装:

```
uv pip install rapidocr-onnxruntime
```

