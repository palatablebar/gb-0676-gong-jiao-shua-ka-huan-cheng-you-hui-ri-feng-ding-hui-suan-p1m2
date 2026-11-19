import csv,io,json
def build_archive(run):
    stream=io.StringIO(newline="");writer=csv.writer(stream)
    writer.writerow(["run_id","input_sha256","rule_version","kind","source_lines","reason","evidence"])
    meta=run["manifest"]
    for row in run["stages"][-1]["rows"]:
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"accepted",json.dumps(row.get("_sources",[])),row.get("status",""),json.dumps(row,ensure_ascii=False)])
    for row in run["stages"][-1].get("cumulative_quarantine",[]):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"quarantine",json.dumps(row.get("_sources",[])),row.get("reason",""),json.dumps(row,ensure_ascii=False)])
    for day in run.get("card_day_totals",[]):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"card_day",json.dumps([]),
                         "每卡日唯一应收",json.dumps(day,ensure_ascii=False,sort_keys=True)])
    if run.get("fallback",{}).get("used"):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"fallback",json.dumps([]),
                         run["fallback"].get("reason",""),json.dumps(run["fallback"],ensure_ascii=False,sort_keys=True)])
    return "﻿"+stream.getvalue()
