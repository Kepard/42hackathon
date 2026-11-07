import json

from mc_observatory.judge import verdict,read_records,watch,evidence,agent_elapsed


def row(step=0,completed=(),next_rung="log",outcome=None):
    return {"step":step,"elapsed_seconds":step*10,"evaluator":{"completed":list(completed),"next":next_rung},
            "tool_calls":[{"name":"seek","arguments":{"target":"oak log","intent":str(step)}}],
            "tool_results":[{"outcome":outcome,"scene_changed":False}],"failure_tags":[]}


def test_rung_budgets_and_new_progress_reset_clock():
    assert verdict([],299) is None
    assert verdict([],301)["reason"]=="rung_time_budget"
    assert verdict([row(20,["log"],"crafting_table")],499) is None
    assert verdict([row(20,["log"],"crafting_table")],501)["rung_age_s"]==301
    assert verdict([row(0,["log"],"iron_ore")],479) is None
    assert verdict([row(0,["log"],"iron_ore")],481)["budget_s"]==480


def test_world_setup_does_not_consume_later_rung_budget():
    from datetime import datetime
    meta={"created_at":"2020-01-01T00:00:00+00:00"}
    r=row(1,["log"],"crafting_table")
    r["finished_at"]="2020-01-01T00:02:10+00:00"
    now=datetime.fromisoformat("2020-01-01T00:07:09+00:00").timestamp()
    elapsed=agent_elapsed(meta,[r],now)
    assert elapsed==309
    assert verdict([r],elapsed) is None
    assert verdict([r],agent_elapsed(meta,[r],now+2))["rung_age_s"]==301


def test_repeated_failures_and_api_errors():
    rows=[row(i,outcome="not_close") for i in range(4)]
    assert verdict(rows,40)["reason"]=="same_failure_four_times: not_close"
    assert verdict(rows[:3],30) is None
    for r in rows:r["failure_tags"]=["api_error","api_timeout"]
    assert "api_timeout" in verdict(rows,40)["reason"]


def test_identical_actions_only_interrupt_when_not_changing_scene():
    rows=[row(i) for i in range(6)]
    assert verdict(rows,60)["reason"]=="identical_actions_without_scene_progress"
    rows[-1]["tool_results"][0]["scene_changed"]=True
    assert verdict(rows,60) is None


def test_partial_trace_tail_is_ignored(tmp_path):
    p=tmp_path/"steps.jsonl";p.write_text(json.dumps(row())+'\n{"partial":')
    assert read_records(p)==[row()]


def test_evidence_cannot_escape_run_folder(tmp_path):
    (tmp_path/"a.png").write_bytes(b"png")
    assert evidence(tmp_path,[{"step":1,"screenshot_ref":"a.png"}])==["judge_evidence/step-1.png"]
    assert evidence(tmp_path,[{"step":2,"screenshot_ref":"../outside.png"}])==[]


def test_watch_only_stops_owned_run_and_never_emits_gameplay_inputs(tmp_path):
    (tmp_path/"run.json").write_text(json.dumps({"status":"running","created_at":"2020-01-01T00:00:00+00:00"}))
    calls=[]
    def request(method,route,payload=None):
        calls.append((method,route,payload))
        return {"active_run":tmp_path.name} if method=="GET" else {"status":"STOPPING"}
    result=watch(tmp_path,request=request)
    assert result["reason"]=="rung_time_budget"
    assert calls==[("GET","/api/status",None),("POST","/api/control",{"action":"stop","source":"devin","text":"judge: rung_time_budget"})]
    assert json.loads((tmp_path/"judge_metadata.json").read_text())["judge_cost"]==0
    assert (tmp_path/"judge_verdict.json").exists()
    calls.clear()
    assert watch(tmp_path,request=lambda *args:{"active_run":"another-run"}) is None
    assert not calls
