# Initial placement assessment

`Initial_Placement_Assessment` checks frame 0 of a selected RGB channel against explicit initial placement requirements for each episode. For example, a rule about a pencil case on the left of the work surface requires a recognizable object and reference frame. An uncalibrated image can leave metric distances, 3D relations and occluded objects undetermined. Select `Initial_State_Distribution` separately for cross-episode grids, entropy and heatmaps.

## Run

1. From the EDC source directory, run `python -m pip install '.[placement,formats]'`.
2. The default model is `qwen3.8-max`. The example uses Alibaba Cloud Model Studio’s Beijing OpenAI-compatible endpoint. Set `DASHSCOPE_API_KEY` in the environment running EDC, using a key for the endpoint’s region and an account with access to the model. [Get an API key](https://help.aliyun.com/zh/model-studio/get-api-key/)
3. Copy the robot configuration and set the camera source and placement rules. The example sets `allow_remote_upload: true` to send the selected frame and task text to the configured service and `enable_thinking: false` for the original method settings. To use another gateway, set its `base_url`, `api_key_env` and served `model`. The service must support image input and JSON output. [Robot configuration](../../../../../../configs/robots/placement_example.yaml)
4. Run:

~~~bash
edc run /path/to/dataset --robot /path/to/robot.yaml \
  --algorithm Initial_Placement_Assessment --output /path/to/results
~~~

For different SOPs, set each episode's `metadata.task_requirements` in the dataset manifest. Episode values override the corresponding shared parameters. Optional `task_description` supplies context. For an HDF5 episode with a mapped RGB channel:

~~~yaml
schema_version: 1
id: pencil_task
episodes:
  - id: recording_001
    path: recording_001.h5
    format: hdf5
    metadata:
      task_requirements: The pencil case starts on the left side of the visible work surface.
      task_description: Put the pencil in the case.
~~~

EDC supplies RGB frames from supported image and video channels. To run both placement and diversity, add the initial distribution configuration to the same robot's `checks` and append `--algorithm Initial_State_Distribution`. Diversity needs its own SAM3 weights, object prompts and comparable camera/scene group.

## Results

Follow `catalog.json` to the per-episode `edc_record_v1` and artifacts. The native `placement_report.json` records the original rule quotes, observations, reasons, `placement_status`, `passed`, `requirements_complete`, uncovered rules and limitations. Any violation gives `incorrect`, including when other rules remain unresolved. Fully confirmed satisfaction gives `correct`. Other outcomes give `undetermined`. `assessment_complete` tracks coverage of the placement requirements. The method has no numeric score.

Provenance, the initial image and input mapping are saved before inference. The raw provider JSON is saved in `provider-response.json`. An invalid response or invented requirement produces an execution error with available evidence retained. Model deployment controls the weight revision. `provenance.json` identifies the requested model and local method fingerprints. See [SOURCE.json](SOURCE.json) for method origin and license status.
