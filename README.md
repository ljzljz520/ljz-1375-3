# 沙漠驿站资料馆

面向沙漠古驿站研究与旅行资料审校的 Web 资料库：以**水井、商队、民居、工艺**四类历史
地理实体组织网页关系；编辑接口保存**历史证据、口述推测、当代提示**三类陈述并记录
**出处与版本**；关系库区分"带证据的关系推断"与"完全人工确认"，公开站只展示已批准连接。

纯 Python 标准库实现（`http.server` + `sqlite3`），零第三方依赖。

## 运行

```bash
python3 run.py [数据库文件] [端口] [基准日]
# 默认: data/archive.db, 8080, 2026-10-07
python3 run.py data/archive.db 8080 2026-10-07
```

首次使用流程（`/editor`）：
1. **运行带证据关系推断** —— 依据陈述生成候选关系（同名 / 行经补水 / 采用工艺）；
2. 在关系表逐条**批准 / 驳回**（或用"登记完全人工确认的关系"直接建立 approved 连接）；
3. **执行后台发布** —— 重建受影响文章与打印卡（经批准关系做闭包级联）。

测试：

```bash
python3 -m unittest discover -s tests   # 17 个服务层 + HTTP 集成测试
```

## 核心规则与落点

| 需求 | 实现 |
| --- | --- |
| 历史实体 / 当前服务实体独立 | `entities`（well/caravan/dwelling/craft）与 `service_points` 分表，无外键派生关系；"沙泉子井"与"沙泉子补给点"只通过独立的 `relations` 行关联，且同名候选带醒目说明 |
| 同名不等于仍可用 | 名称核心相同仅生成 `rtype=same_name` 的**候选**，标注"名称相同不代表同一实体，亦不代表仍可用"，必须人工批准 |
| 陈述出处与版本 | `statements` 每条挂 `sources` 出处；修订产生新版本链（旧版 `superseded`），撤回置 `withdrawn`，不物理删除 |
| 只展示已批准连接 | 公开页 `relations_of(status='approved')`；候选/驳回仅在编辑台可见 |
| 未确定坐标不补图钉 | 历史坐标一律"约圈"，口述指认"待考圈(?)"；服务点 `coord_certain=0` 时只列名不出坐标。地图 `render_map_svg` 无坐标即不渲染 |
| 行程核验 | `check_itinerary` 只接受 reviewed 行程 + 出行日在有效窗口；关闭点 blocked，坐标/服务/证据不足报 gap；附"不自动保证可通行"声明 |
| 发布快照 | `published_docs` / `published_cards` 存哈希快照；网页当前状态只来自发布记录。编辑后显示 stale 横幅，重建后才上线 |
| 级联重建 / 旧任务不复活 | `run_publish_tasks` 对 approved 关系闭包重建；重建内容读当前库，已撤提示、过期许可不会被任何旧任务带上线 |
| 照片许可 | 许可到期后文章不显示影像，只保留来源与说明；按查询日对快照重算 |
| 离线包过期 | `bundles` 带有效期；过期打开显示红色"已过期"横幅，内容仍保留时间窗与来源，不得当作当前依据 |
| 低带宽模式 | `?lowbw=1` 去样式省影像，但**保留必要时间窗与来源说明**；内容从发布快照的数据岛重渲染，不回读编辑库 |

## 数据模型（SQLite）

- `sources` —— 档案 / 口述 / 踏勘 / 照片（含 `license_status`、`license_expires`）
- `entities` / `service_points` —— 历史地理实体 / 当前服务实体（独立）
- `statements` —— 证据 / 口述 / 提示 / 照片陈述，含 `value_type`、版本链、窗口、状态
- `relations` —— `candidate | approved | rejected`，`provenance=inferred | manual`
- `itineraries` —— 已审行程与有效窗口
- `published_docs` / `published_cards` / `publish_tasks` —— 发布快照与后台任务
- `bundles` —— 离线包（含有效期与快照 JSON）

## 种子数据内置的验收场景

- `s2` 红柳井服务区 **2026-02-01 关闭**（基准日 2026-10-07 已阻断）；
- 照片 `S-PHOTO-A7` 许可 **2026-06-30 到期**；
- 井址 `w1` 有档案坐标、口述指认与 2024 考订，`w2` 档案/口述坐标**来源冲突**（>300m）；
- 历史井 `w2` 与服务点 `s2` 的同名候选**故意不批准**用于验证；
- `s3` 野驼点**坐标不确定**；
- 已审行程 `i1` 窗口 2026 全年，经 `s1 → s2`。
