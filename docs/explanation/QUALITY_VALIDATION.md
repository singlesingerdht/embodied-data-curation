# From Quality Checks to Autonomous Data Curation

[Home](../../README.md) · [简体中文](QUALITY_VALIDATION.zh-CN.md) · [Algorithms](../reference/ALGORITHMS.md) · [Selection and export](../how-to/SELECTION.en.md) · [Presentation](../presentations/edc-framework.zh-CN.pptx)

<a id="needs"></a>
## What Users Need, and Why Check the Data?

Users need **data suited to their task and policy, with reasons for selecting it**. Requirements include the task and success criteria, target policy and training stage, required inputs and quality conditions, and data/computing budget. Applicable methods also need expert demonstrations or reliable reference data. Today, [configuration](../how-to/DATA_AND_ROBOT.md) specifies inputs and checks, yielding reports, issue evidence, and [selected copies or symlinks with source manifests](../how-to/SELECTION.en.md). Planned outputs add task annotations, model-specific subsets, and improved data versions.

**Data quality means the reliability of a record as input for a specified training task: required information is complete, values are valid with clear meanings and units, and observations and actions correspond as required.** It concerns whether saved data reliably represents what the robot observed, what it did, and how the two correspond. Demonstration completion and record validity are separate properties.

Necessary conditions are prerequisites for valid training inputs. For grasping training that requires hand-camera images and joint actions, missing images remove required observations; NaN actions remove valid targets; and incorrectly pairing a pre-grasp image with a release action misrepresents their relationship.

> Valid training sample → necessary conditions satisfied; a necessary condition violated → the sample cannot directly be treated as valid.

EDC turns supported requirements into checks, locating problems and retaining evidence. Requirements depend on the task: still images do not establish freezing, and decoding failures may come from the reading environment, so alerts need supporting evidence. **Passing applies only to the conditions actually checked; it does not guarantee better policy learning.**

<a id="layers"></a>
## What Evidence Do the Three Levels Provide?

**Data quality makes records readable and interpretable by machines; task value turns human task understanding into learning signals; policy value uses model feedback to connect human goals with the model's learning direction.**

| Level | Question and evidence | Current status |
|---|---|---|
| **Data-centric: data quality** | Is the record reliable? Compare fields, values, and temporal relationships with explicit input requirements | Shared ingestion, nine CPU quality checks, evidence recording, and selected-data export are available |
| **Task-aware: task value** | What experience is worth learning? Derive local progress, key-operation, and recovery signals from expert demonstrations and task requirements | Research foundations exist; methods await integration |
| **Policy-aware: policy value** | Does this experience suit the target model? Use model learning feedback and reliable references | Research foundations exist; methods await integration |

Cross-robot reuse rests on configuration and shared reading/precheck interfaces. Robots map their respective parts, fields, and units to reuse checks; semantics, physical thresholds, and calibration still require individual confirmation. [Supported formats and conditions](../reference/DATA_AND_ROBOTS.md#formats) specify the boundaries.

Task value concerns the learning content of experience; policy value concerns its usefulness to a specified model. Reliable data need not have equal learning value, and failed trajectories can contain useful experience. Alignment here is tested through selection and learning outcomes, rather than assuming complete understanding of human intent.

<a id="automation"></a>
## What Enables Progressive Automation and Evidence-Based Choices?

**Learn how to assess from demonstrations, test assessments through task and model feedback, and use the results to improve data selection.**

Growing datasets and changing tasks and models make repeated manual assessment, method selection, and rule adjustment costly. Automation becomes feasible when **patterns in experience can be learned, data signals can inform some decisions, and candidate plans have comparable outcomes**. Scale and diversity may help, but their effects depend on coverage, reference reliability, and generalization; volume alone does not establish autonomy.

| Manual judgment to reduce | Automation mechanism | Research support and boundary |
|---|---|---|
| Assess local task progress | Learn local progress and advantage signals from normalized temporal offsets between expert-demonstration frame pairs, then apply them to other records | [STEAM](https://arxiv.org/html/2606.29834v2) supports reusable assessment with less frame-level annotation; applicability to target data requires validation |
| Assess suitability for a target model | Compare candidate gradients with semantically and visually matched clean references, aggregating compatibility into episode scores | [RoboDrop](https://arxiv.org/html/2609.10021v1) provides a model-related selection signal; compatibility is a proxy, with benefits requiring training evaluation |
| Specify how much data to remove | Select a single Gaussian or a two-component mixture from episode scores, then filter using the low-compatibility component and a confidence condition | [RoboDrop Section 4.5](https://arxiv.org/html/2609.10021v1#S4.SS5) uses BIC model selection and can retain all candidates; no preset removal ratio is needed, but confidence parameters and modeling assumptions remain |
| Choose the next algorithms, rules, or processing methods | Compare executable candidates under quality and budget constraints, using validation feedback to guide subsequent choices | An EDC research goal; these papers do not directly validate a complete autonomous decision process |

**Experience can become reusable assessment capability, reducing per-record human judgment. Data signals can support some selection decisions, reducing manually specified filtering rules. Comparing the actual effects of curation plans is the further step needed to reduce human intervention in choosing the next course of action.** Similarity helps find suitable references for comparison; it does not mean more similar data is always more valuable.

Published experiments support specific parts of this mechanism:

- **STEAM combined with CFGRL** achieves **83.5%** average success across four real-robot tasks, versus **58.8%** for the best baseline, supporting demonstration-derived learning signals. [Experiments](https://arxiv.org/html/2606.29834v2#S4)
- **RoboDrop** reports average real-robot task success rising from **35.0% to 67.5%** after curation, supporting model-related data selection. [Experiments](https://arxiv.org/html/2609.10021v1#S5.SS4)

These figures are paper-specific experimental results. STEAM's combined gains include more than filtering.

<a id="evidence"></a>
### Trace the Process and Test the Reasoning

- **Traceable process:** identify source data, algorithms, parameters, and how data was retained or transformed.
- **Verifiable judgment:** explain how evidence relates to the task or model and test it through independent review and training outcomes, beyond retaining a score or log.

A proposed example is adding recovery experience for a grasping policy. Task signals locate “missed grasp—adjustment—success” segments for assessment in their full context; model feedback then assesses learning value. Compare training on successful trajectories alone with training that includes complete recovery segments under comparable budgets. Temporary regression does not directly imply no value. Linking **needs, signals, rules, selected outputs, and validation results** explains choices and informs subsequent adjustments.

<a id="autonomous"></a>
## How to Move Toward Autonomous Improvement

**User needs → annotation and assessment → data selection or improvement → task and training feedback → updates to rules, algorithms, and processing → reassessment.**

Today, EDC provides shared reading, [applicability recommendations and execution](../how-to/RUN_AND_SELECT.md#selection), evidence recording, and [finite-stage workflows](../how-to/WORKFLOWS.en.md). Recommendations check inputs, parameters, and dependencies; they do not identify the optimal algorithm for a user's goal. Workflows require provenance and consistency validation followed by quality rechecks on editing/augmentation branches; outputs that fail checks cannot proceed. Arbitrary segment scheduling and general resampling transforms are not currently supported.

Value methods will be integrated progressively. Beyond the core mechanisms above, [FLORA](https://arxiv.org/abs/2610.31779) offers an external reference for annotation using outcome, process, and timestamped description signals. MGT-QV informs segment curation, while DemoTempo informs demonstration resampling and data improvement. Public links and experimental results for MGT-QV and DemoTempo remain unverified.

**The long-term goal is to compare and adapt curation plans through feedback, enabling continuous autonomous improvement.** Users set goals, necessary quality requirements, and budgets; EDC will explore selection among allowed algorithms, rules, and processing methods, and eventually generate new algorithms for validation before integration. Each iteration should retain plan versions, evidence, costs, and outcomes. Necessary quality requirements must not be relaxed merely to increase retention. The research supports individual parts; complete autonomous decision-making and combined benefits remain to be implemented and validated.

## How to Validate These Advantages

These are planned evaluations, not completed full-framework experiments.

| Claim to test | Evaluation |
|---|---|
| Assessment capability is reusable | Compare progress, segmentation, and key events with independent human judgments on data not used to learn the evaluator; check task and scene coverage |
| Automation reduces manual work | Measure human hours, intervention counts, and computing costs at comparable output quality |
| Retained records are more reliable | Blindly review samples from original, retained, and rejected data; compare defect rates and uncertainty, including mistaken removal of difficult or useful samples |
| Selection improves learning | Compare all original data, a random subset matched to the EDC subset's size, and the EDC subset; evaluate the contributions of quality, task value, and policy value in stages |
| Feedback improves decisions | In future work, compare fixed plans with feedback-adjusted plans under comparable total budgets, recording task performance and costs |

Training comparisons fix the policy, preprocessing, and test conditions, use multiple seeds and repeated random sampling, and report episode counts, duration, success rates, and variation. Keep reference data, evaluator-learning data, rule-adjustment data, and final testing separate. Beating an equally sized random subset supports the selection criteria; also beating the full dataset supports the benefit of removing records in that experiment.

Independent framework validation combines agreement with human judgments, policy training, and task performance. Software tests and repeated quality checks establish implementation behavior; training evaluations establish learning benefits.
