# Embodied Data Curation (EDC) — quality checks

Give EDC a batch of robot episodes and one robot yaml; it runs nine quality algorithms on every episode and writes
one HTML report: each episode and each checked item is **PASS, REVIEW, SALVAGE or EXCLUDE**, with the reason.

A new robot needs only `configs/robots/<name>.yaml`: where joints, end effector, gripper and cameras are stored,
their units (every entry states one; raw readings state their range or a linear scale), and optionally a URDF
(the yaml names, for each recorded joint column, the URDF joint it is). LeRobot v3.0 first; LeRobot v2.1, HDF5, Parquet, ROS1/ROS2 bag, MCAP and
JSON+MP4 episode folders are read too.

```bash
edc channels <data>                                   # names to use in the yaml
edc robot check --robot configs/robots/<name>.yaml --data <episode>       # does the yaml match the data
edc robot urdf --urdf <file> [--base <link> --tip <link>]   # names in a URDF for the yaml's urdf part
edc robot urdf --robot configs/robots/<name>.yaml --data <episode1> <episode2>     # verify the yaml's urdf part against recorded joints and tip
edc batch <data>... --robot configs/robots/<name>.yaml --output report.html
```

Templates: `configs/templates/`. New algorithms: subclass `edc.algorithms.base.QualityAlgorithm`
(example: `examples/plugins/algorithm/joint_jump.py`). Full guide in Chinese: [README.zh-CN.md](README.zh-CN.md).
