import json
from pathlib import Path

from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.decision_export_v4 import convert_progress_episode
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.offline_v3 import _refine_early_regression


def _episode(tmp_path: Path, attr: dict, evaluation: dict) -> tuple[Path, Path]:
    episode = tmp_path / "episode"; source = episode / "subtask_eval"; result = tmp_path / "v3"
    source.mkdir(parents=True); result.mkdir()
    (source / "evaluation_summary.json").write_text(json.dumps(evaluation))
    (source / "pred_vllm.json").write_text("[]")
    (result / "attribution_result.json").write_text(json.dumps(attr))
    return episode, result


def _attr() -> dict:
    return {"rule_version":"progress_attribution_v3","regression_results":{},
        "stagnation_results":{},"proactive_findings":[],"training_events_v3":[]}


def _eval() -> dict:
    return {"video_fps":30,"video_duration_sec":100,"subtask_results":[
        {"start_frame":0,"end_frame":2999,"regressions":[]}]}


def test_v3_training_event_layers(tmp_path):
    attr=_attr(); attr["training_events_v3"]=[
        {"event_id":"low","source_type":"failed_grasp_close","decision":"keep",
         "label":"grasp_progress_fluctuation","reason":"drop below 10%",
         "source_evidence":{"close_frame":100,"dip_frame":110},"evidence":{"progress_drop":.08}},
        {"event_id":"uncertain","source_type":"failed_grasp_close","decision":"review",
         "label":"unconfirmed_grasp_regression","reason":"missing evidence",
         "source_evidence":{"close_frame":200,"dip_frame":220},"evidence":{"progress_drop":.12}},
        {"event_id":"failed","source_type":"failed_grasp_close","decision":"exclude",
         "label":"failed_grasp_close","reason":"confirmed",
         "source_evidence":{"close_frame":300,"dip_frame":330},"evidence":{"progress_drop":.15}}]
    episode,result=_episode(tmp_path,attr,_eval()); decisions,observations,errors=convert_progress_episode(episode,result)
    assert not errors
    assert [(x["decision_id"],x["decision"]) for x in decisions["decisions"]] == [("uncertain","review"),("failed","exclude")]
    assert observations["observations"][0]["decision_id"] == "low"


def test_v4_stagnation_boundaries(tmp_path):
    attr=_attr(); attr["stagnation_results"]={
        "twelve":{"start_frame":0,"end_frame":360,"duration_sec":12,"primary_label":"x"},
        "seven25":{"start_frame":500,"end_frame":710,"duration_sec":7,"primary_label":"x"},
        "five":{"start_frame":1000,"end_frame":1150,"duration_sec":5,"primary_label":"x"},
        "four7":{"start_frame":1500,"end_frame":1641,"duration_sec":4.7,"primary_label":"x"},
        "three":{"start_frame":2300,"end_frame":2390,"duration_sec":3,"primary_label":"x"}}
    evaluation={"video_fps":30,"video_duration_sec":100,"subtask_results":[
        {"start_frame":0,"end_frame":499,"regressions":[]},
        {"start_frame":500,"end_frame":1339,"regressions":[]},
        {"start_frame":1340,"end_frame":1927,"regressions":[]},
        {"start_frame":1928,"end_frame":2999,"regressions":[]}]}
    episode,result=_episode(tmp_path,attr,evaluation); d,o,_=convert_progress_episode(episode,result)
    values={x["decision_id"]:x["decision"] for x in d["decisions"]}
    assert values == {"twelve":"exclude","seven25":"exclude","five":"review","four7":"review"}
    assert [x["decision_id"] for x in o["observations"]] == ["three"]


def test_early_close_requires_ten_percent_no_load_and_no_recovery():
    event={"frame":100,"evidence":{"pre_drop_prog":.72,"cur_spike":False,
        "events_in_win":[[90,"close"]]}}
    evaluation={"subtask_results":[{"regressions":[{"frame_index":100,
        "progress_at_regression":.60,"trough_frame":100,
        "progress_after_7":[.61,.62,.63,.64,.65,.66,.67]}]}]}
    assert _refine_early_regression(event,evaluation)["decision"] == "exclude"
    evaluation["subtask_results"][0]["regressions"][0]["progress_after_7"]=[.70,.70,.71]
    assert _refine_early_regression(event,evaluation)["decision"] == "keep"
