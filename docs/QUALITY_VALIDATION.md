# Quality Checks and Selection Validation

[Home](../README.md) · [简体中文](QUALITY_VALIDATION.zh-CN.md) · [Algorithms](ALGORITHMS.md) · [Selection and export](SELECTION.md)

## Why Check the Data?

A completed demonstration does not guarantee a usable training record. Training needs to know **what the robot observed, what it did, and how the two correspond**.

**A necessary condition is a prerequisite a record must satisfy to qualify as input for a specified training task.** For example, grasping training that requires hand-camera images and joint actions needs usable images, valid action values with known meanings and units, and correct observation–action pairing. If required images are confirmed missing or action values are NaN, the record cannot serve unchanged as a valid sample for that task.

> Valid training sample → necessary conditions satisfied; a necessary condition violated → the sample cannot directly be treated as valid.

EDC uses this rationale to check records, locate problems, and support selection. Necessary conditions come from the task's input requirements; not every algorithm alert establishes a violation. A still image does not establish freezing, and decoding failures may come from the reading environment, so supporting evidence is needed.

**Satisfying necessary conditions establishes the corresponding input prerequisites, but does not establish better policy learning.** Passing applies only to the requirements actually checked. Whether selection helps learning requires the training comparison below.

## How to Validate Selection

The central question is: **does removing these records help learning more than using all data or removing records at random?** The proposed comparison uses three training groups:

| Training data | Purpose |
|---|---|
| All original data | Establish training performance without selection |
| A random subset with the same episode count as the EDC subset | Distinguish informed selection from random removal |
| The EDC subset | Evaluate selection based on check results |

Fix the policy, training budget, preprocessing, and independent test conditions. Use multiple random seeds and repeat random subset sampling. Report data quantities, total duration, task success rates, and variation. Keep data used to tune selection rules separate from final testing.

- Consistently outperforming the equally sized random subset supports the usefulness of the selection criteria for that task.
- Also outperforming all original data supports the benefit of removing some records in that experiment.

Independently sample retained and excluded records to assess false alarms, missed defects, defect prevalence, and task coverage. Use these as supporting information to interpret results and identify mistaken exclusions. **Fewer defects or higher check pass rates alone do not establish better training performance.**

## Current Status

Checks, evidence recording, and copying or symlinking data selected from batch reports are available; see [selection and export](SELECTION.md). Results from the training comparison above are not yet provided, so improved policy performance is not claimed. Software tests and CI validate implementation behavior.
