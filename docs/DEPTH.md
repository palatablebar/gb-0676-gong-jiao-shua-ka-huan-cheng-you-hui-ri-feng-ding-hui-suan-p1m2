# 深度登记 公交刷卡换乘优惠与日封顶回算

规模：中型。使用者：公交清分中心复核员。核心规则：换乘按上次有效付费行程的时间窗计算，优惠与日封顶按确定顺序裁决并保留分币守恒。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 刷卡采信 | 查看并运行刷卡采信 | `/api/taps` | `app/modules/m1_taps/rules.py` | `tests/test_m1.py` |
| M2 线路换乘 | 查看并运行线路换乘 | `/api/routes` | `app/modules/m2_routes/rules.py` | `tests/test_m2.py` |
| M3 票价版本 | 查看并运行票价版本 | `/api/fares` | `app/modules/m3_fares/rules.py` | `tests/test_m3.py` |
| M4 行程配对 | 查看并运行行程配对 | `/api/journeys` | `app/modules/m4_journeys/rules.py` | `tests/test_m4.py` |
| M5 优惠回算 | 查看并运行优惠回算 | `/api/discounts` | `app/modules/m5_discounts/rules.py` | `tests/test_m5.py`、`tests/test_m5_passes.py` |
| M6 清分对账 | 查看并运行清分对账 | `/api/clearing` | `app/modules/m6_clearing/rules.py` | `tests/test_m6.py` |
| M7 日结归档 | 查看并运行日结归档 | `/api/settlement` | `app/modules/m7_settlement/rules.py` | `tests/test_m7.py` |

月票判定与卡日归并集中在 `app/passes.py`；整链路的唯一应收汇总在 `app/pipeline.py` 输出为 `card_day_totals`。

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧编码 | 默认解码失败 | 回退GB18030 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_legacy_encoding_real_sample` |
| TRAP-02 | BOM | 首列名多字符 | utf-8-sig | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_bom_real_sample` |
| TRAP-03 | 带引号换行 | 拆成两条 | csv逻辑行 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_quoted_newline_is_one_record` |
| TRAP-05 | 列数不匹配 | 静默错列 | 隔离原行 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_bad_columns_real_sample` |
| TRAP-08 | 前导零ID | 编码变形 | 全程字符串 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_identifier_preserved_real_sample` |
| TRAP-14 | 重复键冲突 | 后写覆盖 | 保留首条并隔离冲突行 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_duplicate_conflict_real_sample` |
| TRAP-18 | 空态三态 | 一律成功 | 分别错误码 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_three_empty_states` |
| TRAP-26 | 旧版本提交 | 覆盖新结果 | 版本条件更新 | `app/store.py` | `tests/test_e2e.py::test_stale_review_409` |
| TRAP-06 | 全角数字 | 转换失败 | NFKC与Decimal | `app/values.py` | `tests/test_m3.py::test_thousands_and_fullwidth` |
| TRAP-07 | 金额哨兵 | 零参与扣费 | 空值隔离 | `app/values.py` | `tests/test_m3.py::test_real_sentinel_quarantined` |
| TRAP-11 | 混合偏移 | 落错版本 | UTC归整 | `app/values.py` | `tests/test_m3.py::test_timezone_equal` |
| TRAP-23 | 换乘截止点 | 重复优惠 | 截止点排除 | `app/modules/m5_discounts/rules.py` | `tests/test_m5.py::test_exact_window_excluded` |
| TRAP-25 | 票价重叠 | 随意取版 | 冲突隔离 | `app/modules/m3_fares/rules.py` | `tests/test_m3.py::test_overlap_rejected` |
| TRAP-27 | 零扣费月票 | 不当换乘前序，后续现金丢优惠 | 月票作前序但不推进窗口时钟、不占封顶 | `app/modules/m5_discounts/rules.py`、`app/passes.py` | `tests/test_m5_passes.py::test_pass_is_transfer_precursor` |
| TRAP-28 | 连续刷月票 | 每次重置换乘窗口反复续期 | 窗口只由最近实际付费行程起算 | `app/modules/m5_discounts/rules.py` | `tests/test_m5_passes.py::test_consecutive_passes_do_not_renew_window` |
| TRAP-29 | 封顶零扣费 | 误当月票、重复计前序 | 先按现金规则算出本应扣费，>0 才判为月票 | `app/passes.py` | `tests/test_m5_passes.py::test_cap_zero_row_is_not_pass` |
| TRAP-30 | 同批重跑 | 换乘/封顶额度重复占用 | 输入摘要唯一批次 + 事件去重 + 卡日归并 | `app/store.py`、`app/passes.py` | `tests/test_monthly_e2e.py::test_rerun_same_batch_does_not_double_charge` |
| TRAP-31 | 月票核验失败 | 新结果覆盖旧版 | 回退旧口径并保留旧批次，失败只写审计 | `app/pipeline.py`、`app/store.py` | `tests/test_monthly_e2e.py::test_pass_verification_failure_keeps_previous` |
| TRAP-32 | 对账未平封存 | 差异被静默日结 | 对账不平拒绝归档 | `app/modules/m7_settlement/rules.py` | `tests/test_monthly_e2e.py::test_unreconciled_cannot_seal` |

## 对账规则

输入原始逻辑行=最终采纳来源行+隔离来源行+重复行+空白行+分段表头。派生记录用_sources保存完整来源，因此分组不丢行。业务预期合计与上游申报合计之差等于逐行差异之和。结果按输入摘要、规则版本及参数产生唯一批次，事务写入；同源重跑只复用已有批次。

每卡每业务日只产生一份汇总应收（`card_day_totals`）：换乘优惠、封顶减免、月票覆盖与应扣均在该卡日内唯一，页面、对账与导出共用同一份，避免重跑重复占额。M6 若发现来源行不平或 `reconciled=false`，M7 拒绝封存日结。

## 月票裁决

零扣费行程（基础票价>0、实扣=0、且按现金规则本应扣费>0）视为月票覆盖。它可作为后续现金行程的换乘前序，参与线路比较与页面展示；但不移动换乘窗口的起算时钟，也不占用日封顶。换乘窗始终由最近一次实际付费行程起算，当日尚无付费行程时由首个行程起算，因此连续刷月票不会反复续期。封顶导致的零扣费（本应扣费已被封顶吸收）仍是现金行程，不是月票。

月票状态核验失败时回退旧版口径（只有实扣>0的行程可作锚点），并保留上一次核验通过的旧批次：失败尝试只追加到 attempts 审计表，不产生新批次，重跑同一输入不会重复占额。

## 已知不覆盖

4MB以上文件需预先按业务键分批；不模拟生产支付与外部回调。人工复核只变更批次状态，不改写原始记录。
