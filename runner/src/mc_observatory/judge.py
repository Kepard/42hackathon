"""Observe traces and stop wasteful runs; never emits gameplay inputs or goals."""
import argparse
import json
import shutil
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from .vision import changes


FAILURES = {"not_close", "not_in_reach", "no_change", "no_progress_cue", "stuck", "timeout", "vision_error", "not_visible"}


def failure(row):
    tags = row.get("failure_tags", [])
    if tags:
        return "+".join(sorted(tags))
    for result in row.get("tool_results", []):
        outcomes = result.get("outcome")
        for outcome in outcomes if isinstance(outcomes,list) else [outcomes]:
            if outcome in FAILURES:
                return outcome
        if result.get("stuck"):
            return "stuck"
    return None


def verdict(records, elapsed):
    completed, progressed, next_rung = set(), 0, "log"
    for row in records:
        evaluation = row.get("evaluator", {})
        new = set(evaluation.get("completed", [])) - completed
        if new:
            completed.update(new)
            progressed = row.get("elapsed_seconds",0)
        next_rung = evaluation.get("next",next_rung)
    budget = 480 if any(word in (next_rung or "") for word in ("iron","obsidian","bucket","portal","flint","nether")) else 300
    reason = None
    recent = [failure(row) for row in records[-4:]]
    if len(recent)==4 and recent[0] and len(set(recent))==1:
        reason = "same_failure_four_times: " + recent[0]
    if len(records)>=4:
        common=set(records[-1].get("failure_tags") or [])-{"scene_change","realtime_motion_unobserved"}
        for row in records[-4:-1]: common.intersection_update(row.get("failure_tags") or [])
        if common: reason = "same_failure_four_times: " + "+".join(sorted(common))
    actions = []
    for row in records[-6:]:
        calls = [{"name": call["name"], "arguments": {k:v for k,v in call.get("arguments",{}).items() if k!="intent"}}
                 for call in row.get("tool_calls",[]) if call.get("name")!="update_notes"]
        actions.append(json.dumps(calls,sort_keys=True) if calls else None)
    signals=[]
    for row in records[-6:]:
        if row.get("tool_results"):
            signals.append(any(result.get("scene_changed") for result in row["tool_results"]))
        elif "judge_scene_changed" in row:
            signals.append(row["judge_scene_changed"])
        elif "scene_changed" in row:
            signals.append(row["scene_changed"])
        elif "frame_change" in row:
            signals.append(row["frame_change"] >= .002)
    unchanged = len(signals)>=5 and not any(signals)
    if len(actions)==6 and actions[0] and len(set(actions))==1 and unchanged:
        reason = reason or "identical_actions_without_scene_progress"
    if next_rung and elapsed-progressed > budget:
        reason = reason or "rung_time_budget"
    if reason:
        return {"reason":reason,"elapsed_s":elapsed,"rung_age_s":elapsed-progressed,"budget_s":budget,
                "completed":sorted(completed),"next_rung":next_rung,"steps":[r.get("step") for r in records[-6:]],
                "recent_failures":recent}
    return None


def read_records(path):
    if not path.exists(): return []
    rows=[]
    lines=path.read_text().splitlines()
    for i,line in enumerate(lines):
        try: rows.append(json.loads(line))
        except json.JSONDecodeError:
            if i!=len(lines)-1: raise
    for row,following in zip(rows,rows[1:]):
        if row.get("tool_results"): continue
        before=frame_path(path.parent,row.get("screenshot_ref"))
        after=frame_path(path.parent,following.get("screenshot_ref"))
        if before is None or after is None: continue
        try:
            delta=changes(before.read_bytes(),after.read_bytes())
            row["judge_scene_changed"]=delta["frame_change"]>=.002
        except ValueError:
            row["judge_scene_changed"]=True
        except OSError:
            pass
    return rows


def frame_path(run_dir, ref):
    if not isinstance(ref,str): return None
    source=(run_dir/ref).resolve()
    if source.is_relative_to(run_dir.resolve()) and source.suffix.lower() in (".png",".jpg",".jpeg") and source.is_file():
        return source
    return None


def evidence(run_dir, records):
    saved=[]
    for row in records[-3:]:
        source=frame_path(run_dir,row.get("screenshot_ref"))
        if source is None: continue
        folder=run_dir/"judge_evidence";folder.mkdir(exist_ok=True)
        destination=folder/f"step-{row.get('step')}{source.suffix}"
        shutil.copyfile(source,destination);saved.append(str(destination.relative_to(run_dir)))
    return saved


def http(backend, method, route, payload=None):
    data=None if payload is None else json.dumps(payload).encode()
    request=urllib.request.Request(backend+route,data=data,method=method,headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(request,timeout=10) as response:
        return json.load(response)


def agent_elapsed(meta, rows, now):
    if rows and rows[-1].get("finished_at"):
        latest=rows[-1]
        return latest["elapsed_seconds"]+max(0,now-datetime.fromisoformat(latest["finished_at"]).timestamp())
    return now-datetime.fromisoformat(meta["created_at"]).timestamp()


def watch(run_dir, backend="http://127.0.0.1:8000", interval=60, request=None):
    run_dir=Path(run_dir)
    request=request or (lambda method,route,payload=None: http(backend,method,route,payload))
    (run_dir/"judge_metadata.json").write_text(json.dumps({"judge_cost":0.0,"model":None,"policy":"heuristic-observe-and-stop","interval_s":interval}))
    while True:
        meta=json.loads((run_dir/"run.json").read_text())
        if meta.get("status") not in ("starting","running") or (run_dir/"summary.json").exists(): return None
        rows=read_records(run_dir/"steps.jsonl")
        elapsed=agent_elapsed(meta,rows,time.time())
        decision=verdict(rows,elapsed)
        note={"at":time.time(),"elapsed_s":elapsed,"steps":len(rows),"verdict":decision or "progressing"}
        with (run_dir/"judge_notes.jsonl").open("a") as stream: stream.write(json.dumps(note)+"\n")
        if decision:
            if request("GET","/api/status").get("active_run") != run_dir.name: return None
            decision["evidence_frames"]=evidence(run_dir,rows)
            decision["judge_cost"]=0.0
            decision["stop_response"]=request("POST","/api/control",{"action":"stop","source":"devin","text":"judge: "+decision["reason"]})
            (run_dir/"judge_verdict.json").write_text(json.dumps(decision,indent=2))
            print("JUDGE_INTERRUPT",json.dumps(decision),flush=True)
            return decision
        time.sleep(interval)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir",type=Path)
    parser.add_argument("--backend",default="http://127.0.0.1:8000")
    parser.add_argument("--interval",type=float,default=60)
    args=parser.parse_args()
    if args.interval<=0: parser.error("interval must be positive")
    watch(args.run_dir,args.backend,args.interval)


if __name__=="__main__": main()
