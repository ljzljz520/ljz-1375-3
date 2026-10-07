# 沙漠驿站资料馆

围绕**水井、商队、民居、工艺**组织的荒漠驿站资料馆 Web 应用。支持编辑接口保存
**历史证据、口述推测、当代提示**三类陈述并全程留版本，关系数据库记录每条陈述的
出处与版本；公共网站只展示**服务端实际发布记录**中已批准的连接。

纯 Python 标准库 + SQLite + 原生前端，无外部依赖。

## 运行

```bash
./run.sh                       # 自动播种到 data/archive.db，监听 :8000
# 或：python3 -m app.seed data/archive.db && python3 -m app.server 8000 data/archive.db
python3 -m unittest discover -s/tests   # 18 项测试（含 5 个验收场景）
```

打开 http://127.0.0.1:8000 ——「浏览 / 关系图 / 旅行资料检查 / 编辑台（内部）」四个页签。

## 领域模型（表）

| 表 | 作用 |
|---|---|
| `source` | 来源：`historical` 历史证据 / `oral` 口述 / `contemporary` 当代提示 |
| `entity` | 实体：水井 / 商队 / 民居 / 工艺 |
| `statement` | 陈述：断言内容、来源、类别（证据/推测/提示）、审批状态、`supersedes` 修订链 |
| `statement_version` | **每次新建、编辑、审批、撤回都追加一行**，完整出处与版本留痕 |
| `relation` | 实体间关系，必须由一条陈述支撑，`approved` 后才在前台显示 |
| `conflict` | 同实体同属性不同来源的不一致；只登记供人工考辨，不自动取舍 |
| `location` | `exact` / `approximate` / `unlocated`；精确图钉须有已批准陈述 |
| `photo` | 许可类型与截止日；到期自动转 `permission_lapsed`，不发布 |
| `supply_point` / `service_window` | **当代服务实体**的状态与有效窗口 |
| `itinerary` / `segment` | 用户可选的**已审**行程与其有效窗口 |
| `publish_task` / `publication` | 发布队列（可取消）与每实体的不可变修订版本（含正文与打印卡） |
| `offline_package` | 离线包载荷与过期日；另存低带宽版本 |

## 核心规则（均由 `app/core.py` 强制，测试覆盖）

1. **同名不等于关联。** 演示数据中清末井址「红沙井」与当代补给点「红沙井」是两个
   独立实体（`well_old` / `supply_new`）。由口述“可能是同一处”生成的关系停留在待批，
   接口拒绝在支撑陈述未批准时批准关系（`statement_not_approved`）；只有经人工核对、
   有独立证据的 `serves` 关系批准后，旧井址才通过当代补给点获得补给判定。
2. **证据推断与人工确认分离。** 新陈述一律 `proposed`；编辑会把已批准陈述打回待批并
   撤销其关系批准；`/api/graph`、发布正文只渲染 `approved + active` 的关系。
3. **未确定坐标不补成精确图钉。** `exact` 坐标要求已批准陈述，否则报 `unverified_pin`；
   近似点画虚环、未定位实体仅列入清单。
4. **旅行资料检查**（`core.travel_check`）：
   - 只接受 `reviewed=1` 且日期在行程窗口内的用户选定行程，否则分别报
     `itinerary_not_reviewed` / `outside_window`；
   - 每段只读取**最新发布版本**与服务窗口；历史井址仅当存在已批准 `serves` 关系且
     对方当日开放时才判为可补给；
   - 关闭、无窗口、未发布、无实体都成为显式「缺口」，返回 `guaranteed_passage=false`
     和免责声明——**系统不以算法自动保证可通行**。
5. **发布**：编辑只改工作稿并对受影响实体排队；执行队列时按数据库当前状态重建正文与
   **打印卡**（版本号递增）。撤下的当代提示若尚未发布则永远不会出现；**已取消的旧任务
   不会再把撤下提示重新上线**。
6. **照片许可失效**：超过 `license_until` 自动失效并在下次发布中移除。
7. **离线包过期**：超期返回 `410 package_expired`；低带宽包省略照片与坐标数字，但保留
   每条陈述的来源、引注、日期与服务窗口；网页低带宽开关同理。
8. 网页“当前状态”永远来自 `publication`（服务端实际发布记录），工作稿实体在前台完全
   不可见。

## 五个验收场景 → 测试对应

| 场景 | 测试 |
|---|---|
| 补给点关闭 | `test_scenario_1_supply_point_closure` |
| 井址考证修订（版本链、旧说自动撤回、重发） | `test_scenario_2_well_site_revision` |
| 来源冲突（显式登记、人工裁决） | `test_scenario_3_source_conflict_is_explicit` |
| 照片许可失效（时间推进后重发即移除） | `test_scenario_4_photo_license_lapse` |
| 离线包过期 / 低带宽保留时间与来源 | `test_scenario_5_offline_package_expiry`、`test_low_bandwidth_package_keeps_time_and_sources` |
| 旧任务不得让撤回提示复活 | `test_old_task_cannot_resurrect_withdrawn_tip` |
| 同名独立 / 精确图钉限制 / 只发布已批准 | `test_same_name_is_not_a_link`、`test_unverified_coordinates_cannot_become_pin`、`test_website_only_reads_publication_table` |
| HTTP 全链路 | `tests/test_http.py`（5 项） |

## API 摘要

公共（只读发布面）：`GET /api/browse`、`/api/article?entity_id=&lowbw=`、`/api/graph`、
`/api/itineraries`、`/api/card`、`POST /api/travel-check`、`GET /api/package/{id}`。

编辑台：`/api/admin/{sources,entities,statements,statement/{id}/approve|withdraw|edit|revise,
conflicts,conflict/{id},relations,relation/{id}/approve|deactivate,locations,photos,supply,
supply-window,itineraries,segment,review-itinerary,publish,run-publish,cancel-task,tasks,
publications,package}`。
