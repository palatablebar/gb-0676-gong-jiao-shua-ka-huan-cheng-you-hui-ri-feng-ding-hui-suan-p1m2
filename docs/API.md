# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。样例含dirty.csv、clean.csv、legacy.csv与monthly.csv（月票换乘）。/api/runs使用完整流水线并原子保存，返回stages、card_day_totals与fallback；parameters可含pass_verification标记月票核验状态（failed等表示失败）。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV，含每卡日唯一应收与回退记录。月票核验失败时返回旧批次内容并带preserved=true与incoming审计字段，不新建批次；原批次version与decision不变。对账未平（reconciled=false）时/api/settlement返回422 UNRECONCILED。
